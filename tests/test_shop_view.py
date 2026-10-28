"""shop_view.py: the open shop found on screen at any resolution / HUD scale, the next item's icon found
in it, the ring only when both are known. Synthetic shop renders (drawn here) + the user's real 2026
screenshot with the shop open (tests/fixtures/ingame4_2000x1125.jpg, French client)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from treeaicoach import item_icons
from treeaicoach import shop_view as sv

FIX = Path(__file__).parent / "fixtures"
GOLD = (80, 112, 128)          # BGR of the frame border (measured: hue ~20, S ~100, V ~130)
NAVY = (26, 18, 2)             # BGR of the panel (hue ~100, V ~26)
DIVIDER = (60, 64, 50)


def background(w: int, h: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.integers(0, 255, (h // 8 + 1, w // 8 + 1, 3), dtype=np.uint8)
    img = cv2.resize(noise, (w, h), interpolation=cv2.INTER_CUBIC)
    img = (img.astype(np.float32) * np.array([0.35, 0.55, 0.40])).astype(np.uint8)   # greenish map, dim
    return img


def icon_bgr(iid: int) -> np.ndarray:
    rgba = item_icons.item_rgba(iid)
    assert rgba is not None, iid
    return np.ascontiguousarray(rgba[..., [2, 1, 0]])


def render_shop(w: int, h: int, frac_h: float, top: float = 0.12, dx: float = 0.01,
                items: tuple[int, ...] = (), dim: float = 1.0, seed: int = 0, aspect: float = 1.40,
                border: bool = True) -> tuple[np.ndarray, tuple[int, int, int, int], list]:
    """A screen with the shop drawn as the game draws it (frame, panel, tabs line, divider, a row of
    item icons in the list part). Returns the image, the frame rect and the icon rects."""
    img = background(w, h, seed)
    fh = int(frac_h * h)
    fw = int(aspect * fh)
    x0 = int(w / 2 - fw / 2 + dx * fw)
    y0 = int(top * h)
    bw = max(1, int(round(fh / 260)))          # measured: 3 px for a 774 px frame
    cv2.rectangle(img, (x0, y0), (x0 + fw, y0 + fh), NAVY, -1)
    if border:
        cv2.rectangle(img, (x0, y0), (x0 + fw, y0 + fh), GOLD, bw)
    xd = x0 + int(0.61 * fw)
    cv2.line(img, (xd, y0 + int(0.02 * fh)), (xd, y0 + fh - 2), DIVIDER, max(1, bw // 2))
    cv2.line(img, (x0 + 4, y0 + int(0.072 * fh)), (xd - 4, y0 + int(0.072 * fh)), DIVIDER, 1)
    rects = []
    side = int(0.057 * fh)
    for i, iid in enumerate(items):
        ix, iy = x0 + int(0.03 * fw) + i * int(side * 1.55), y0 + int(0.82 * fh)
        ico = cv2.resize(icon_bgr(iid), (side, side), interpolation=cv2.INTER_AREA)
        img[iy:iy + side, ix:ix + side] = (ico.astype(np.float32) * dim).astype(np.uint8)
        cv2.rectangle(img, (ix - 1, iy - 1), (ix + side, iy + side), GOLD, 1)
        rects.append((ix, iy, side, side))
    return img, (x0, y0, fw, fh), rects


def close(a, b, tol: float) -> bool:
    return all(abs(int(p) - int(q)) <= tol for p, q in zip(a, b))


@pytest.mark.parametrize("w,h", [(1280, 720), (1920, 1080), (2560, 1440), (3840, 2160), (2000, 1125)])
@pytest.mark.parametrize("frac_h,top", [(0.50, 0.20), (0.69, 0.146), (0.77, 0.058), (0.90, 0.04)])
def test_frame_found_at_any_resolution_and_hud_scale(w, h, frac_h, top):
    img, rect, _ = render_shop(w, h, frac_h, top)
    got = sv.find_shop(img)
    assert got is not None, (w, h, frac_h)
    assert close(got.rect, rect, 0.02 * rect[3] + 3), (got.rect, rect)


def test_no_shop_no_frame():
    assert sv.find_shop(background(1920, 1080)) is None
    for kw in ({"aspect": 2.3, "frac_h": 0.4}, {"border": False}):      # scoreboard-like / no gold frame
        img, _r, _ = render_shop(1920, 1080, **{"frac_h": 0.7, **kw})
        assert sv.find_shop(img) is None, kw
    assert sv.find_shop(None) is None and sv.find_shop(np.zeros((50, 50, 3), np.uint8)) is None


@pytest.mark.parametrize("w,h,frac_h", [(1280, 720, 0.69), (1920, 1080, 0.77), (2560, 1440, 0.6)])
def test_item_found_in_the_list_dimmed_too(w, h, frac_h):
    items = (6631, 3071, 3053, 3742)
    img, rect, rects = render_shop(w, h, frac_h, items=items, dim=0.55)        # unaffordable: dimmed
    shop = sv.find_shop(img)
    assert shop is not None
    for iid, r in zip(items, rects):
        spot = sv.find_item(img, shop, item_icons.item_rgba(iid))
        assert spot is not None and spot.in_list and close(spot.rect, r, 0.25 * r[2] + 2), (iid, spot, r)
    assert sv.find_item(img, shop, item_icons.item_rgba(1055)) is None          # not shown: no ring
    assert sv.find_item(img, shop, None) is None


def test_real_2026_shop_screenshot():
    """The user's French client, shop open on "Conseillés" (Estropieur selected), 2000x1125."""
    img = cv2.imread(str(FIX / "ingame4_2000x1125.jpg"))
    shop = sv.find_shop(img)
    assert shop is not None and close(shop.rect, (474, 164, 1084, 774), 12), shop
    for iid, xy in ((6631, (499, 801)), (3071, (982, 801)), (3053, (1051, 801))):   # "Achats courants" row
        spot = sv.find_item(img, shop, item_icons.item_rgba(iid))
        assert spot is not None and spot.in_list and close(spot.rect[:2], xy, 8), (iid, spot)
    assert sv.find_item(img, shop, item_icons.item_rgba(1055)) is None          # Doran's blade: not shown
    for name in ("ingame3_2000x1125.jpg", "hud_ingame_1280.jpg"):                # shop closed
        other = cv2.imread(str(FIX / name))
        assert other is not None and sv.find_shop(other) is None, name


def test_watcher_draws_only_when_shop_open_and_item_seen():
    img, _rect, rects = render_shop(1920, 1080, 0.7, items=(6631, 3071))
    closed = background(1920, 1080, 3)
    screen = {"img": img}
    grabs = []
    t = [100.0]

    def grab():
        grabs.append(1)
        return screen["img"], (1920, 0)                  # game on the second monitor

    w = sv.ShopWatcher(grab=grab, clock=lambda: t[0], threaded=False)
    assert w.update([6631], can_shop=False) is None and not grabs        # not in base: never captures
    hl = w.update([6631], can_shop=True)
    assert hl is not None and hl.item_id == 6631 and w.shop_open
    assert close(hl.rect, (rects[0][0] + 1920, rects[0][1], rects[0][2], rects[0][3]), 6)
    n = len(grabs)
    t[0] += 0.2
    assert w.update([6631], True) == hl and len(grabs) == n               # rate limited, last ring kept
    t[0] += sv.LOOK_EVERY_S
    assert w.update([1055, 3071], True).item_id == 3071                  # first id seen in the shop wins
    screen["img"] = closed
    t[0] += sv.LOOK_EVERY_S
    assert w.update([6631], True) is None and not w.shop_open            # shop closed: card only
    assert w.update([6631], False) is None


def test_watcher_never_raises_and_goes_stale():
    t = [0.0]
    w = sv.ShopWatcher(grab=lambda: 1 / 0, clock=lambda: t[0], threaded=False)
    assert w.update([6631], True) is None
    img, _r, _ = render_shop(1920, 1080, 0.7, items=(6631,))
    w2 = sv.ShopWatcher(grab=lambda: (img, (0, 0)), clock=lambda: t[0], threaded=False)
    assert w2.update(6631, True) is not None
    w2._at = t[0] + 100                                   # no new look possible: the old ring expires
    t[0] += sv.STALE_S + 0.1
    assert w2.update(6631, True) is None


def test_ring_patch_leaves_the_icon_visible():
    img, x, y = sv.ring_patch((500, 300, 40, 40))
    assert img.dtype == np.uint8 and img.shape[2] == 4 and x < 500 and y < 300
    cx, cy = 500 - x + 20, 300 - y + 20
    assert img[cy, cx, 3] == 0 and img[:, :, 3].max() == 255
    assert (img[..., :3] <= img[..., 3:4]).all()          # premultiplied
    assert sv.ring_patch((0, 0, 2, 2)) is None
