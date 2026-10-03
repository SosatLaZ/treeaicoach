"""Contact sheet of the in-game overlay at every screen format (development tool, never imported
by the app).

For each format (720p .. 4K, 16:10, 4:3, ultrawide 21:9, a windowed game, League's minimap scale
small / large, League's HUD scale 0 / 1) it lays out and renders EVERY element exactly like the
overlay does (``tools/layout_audit.place_new``: compact + danger card, toast + big banner, timers
strip, big + small play badges, minimap layer, in-world ward markers), measures

* overlaps with League's UI zones and between our elements (must be 0),
* the size of each element (px and % of the screen) and its main text size (px),

and writes ``sheet.png`` (every format, same tile height), ``zoom_<format>.png`` (the bottom-right
quarter and the top-centre band at 1:1: what the player really sees) and ``formats.txt``.

``python -m tools.overlay_formats_sheet [--out DIR] [--no-zoom]``
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools import layout_audit as LA  # noqa: E402
from treeaicoach import fx_render as fx  # noqa: E402
from treeaicoach import layout as L  # noqa: E402
from treeaicoach import overlay as ov  # noqa: E402
from treeaicoach import overlay_render as orr  # noqa: E402
from treeaicoach import toasts as tst  # noqa: E402

#: (name, screen w, h, minimap side as a fraction of the height, window offset, League GlobalScale)
FORMATS: tuple[tuple[str, int, int, float, tuple[int, int], Any], ...] = (
    ("1280x720", 1280, 720, 0.236, (0, 0), None),
    ("1366x768", 1366, 768, 0.236, (0, 0), None),
    ("1600x900", 1600, 900, 0.236, (0, 0), None),
    ("1920x1080", 1920, 1080, 0.236, (0, 0), None),
    ("1920x1080_mm_small", 1920, 1080, 0.17, (0, 0), None),
    ("1920x1080_mm_large", 1920, 1080, 0.30, (0, 0), None),
    ("1920x1080_hud_max", 1920, 1080, 0.236, (0, 0), 1.0),
    ("2560x1440", 2560, 1440, 0.236, (0, 0), None),
    ("3840x2160", 3840, 2160, 0.236, (0, 0), None),
    ("2560x1080_uw", 2560, 1080, 0.236, (0, 0), None),
    ("3440x1440_uw", 3440, 1440, 0.236, (0, 0), None),
    ("1920x1200_16x10", 1920, 1200, 0.236, (0, 0), None),
    ("1680x1050_16x10", 1680, 1050, 0.236, (0, 0), None),
    ("1440x1080_4x3", 1440, 1080, 0.236, (0, 0), None),
    ("1600x900_windowed", 1600, 900, 0.236, (160, 90), None),
)


def _shot(name: str, w: int, h: int, frac: float, off: tuple[int, int]) -> LA.Shot:
    side = int(round(h * frac))
    m = max(4, int(round(h * 0.009)))
    ox, oy = off
    return LA.Shot(name, (ox, oy, w, h), (ox + w - side - m, oy + h - side - m, side, side), size=(w, h),
                   state="format", real=False)


def _font_px(placed: list[LA.Placed], group: str) -> float:
    return max((p.font_px for p in placed if p.group == group), default=0.0)


def run(out: Path, zoom: bool = True) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    tiles: list[np.ndarray] = []
    rows: list[dict[str, Any]] = []
    for name, w, h, frac, off, gscale in FORMATS:
        L.set_game_hud_scale(gscale)
        try:
            shot = _shot(name, w, h, frac, off)
            zones = L.game_zones(shot.screen, shot.minimap, hud_scale=L.game_hud_scale())
            states = LA.sample_states(shot)
            scenes, lay = LA.place_new(shot, states)
            placed = scenes["normal"]
            res = LA.overlaps(shot, placed, zones)
            mm_img, _tags = LA._minimap_with_tags(orr, states["minimap"], shot.minimap)
            bad = {o["element"] for o in res["game"]} | {o["element"] for o in res["ours"]}
            area = float(w * h)
            sizes = {}
            for p in placed:
                bb = p.bbox()
                if bb is None:
                    continue
                cur = sizes.get(p.group)
                if cur is None or bb[2] * bb[3] > cur[2] * cur[3]:
                    sizes[p.group] = bb
            row = {"format": name, "scale_card": round(L.overlay_scale(shot.screen, "card"), 3),
                   "card_w": ov.hud_width(shot.screen),
                   "fonts": {g: round(_font_px(placed, g), 1) for g in ("card", "toasts", "timers", "badge_big")},
                   "sizes": {g: (bb[2], bb[3], round(100.0 * bb[2] * bb[3] / area, 2)) for g, bb in sizes.items()},
                   "game_overlaps": len(res["game"]), "our_overlaps": len(res["ours"]),
                   "details": [f"{o['element']} x {o['with']} ({o['area']})" for o in res["game"] + res["ours"]],
                   "slots": {n: s.anchor for n, s in lay.slots.items()}}
            rows.append(row)
            title = (f"{name}: UI jeu {row['game_overlaps']}, entre nous {row['our_overlaps']}, carte "
                     f"{row['fonts']['card']}px")
            img = LA.compose(shot, placed, zones, None, mm_img, title, bad)
            th = 540
            tiles.append(cv2.resize(img, (int(img.shape[1] * th / img.shape[0]), th), interpolation=cv2.INTER_AREA))
            if zoom:
                # the composite is in screen-local px (background() origin = the screen origin)
                br = img[int(h * 0.45):h, int(w - min(w, 1.05 * h)):w]
                top = img[0:int(h * 0.42), int(w / 2 - 0.45 * h):int(w / 2 + 0.45 * h)]
                cv2.imwrite(str(out / f"zoom_{name}_br.png"), cv2.cvtColor(br, cv2.COLOR_RGB2BGR))
                cv2.imwrite(str(out / f"zoom_{name}_top.png"), cv2.cvtColor(top, cv2.COLOR_RGB2BGR))
        finally:
            L.set_game_hud_scale(None)
    # sheet: 3 tiles per row, padded to the widest tile
    W = max(t.shape[1] for t in tiles)
    padded = [np.pad(t, ((0, 0), (0, W - t.shape[1]), (0, 0)), constant_values=255) for t in tiles]
    while len(padded) % 3:
        padded.append(np.full_like(padded[0], 255))
    grid = np.vstack([np.hstack(padded[i:i + 3]) for i in range(0, len(padded), 3)])
    cv2.imwrite(str(out / "sheet.png"), cv2.cvtColor(grid, cv2.COLOR_RGB2BGR))
    lines = ["TreeAI Coach - overlay a chaque format (tools/overlay_formats_sheet.py)", ""]
    for r in rows:
        flag = "OK " if not r["game_overlaps"] and not r["our_overlaps"] else "!! "
        lines.append(f"{flag}{r['format']:<22} echelle carte {r['scale_card']:.2f}  carte {r['card_w']} px  "
                     f"polices {r['fonts']}")
        for g, (bw, bh, pct) in r["sizes"].items():
            lines.append(f"      {g:<13} {bw:>4} x {bh:<4} {pct:5.2f} % de l'ecran  ({r['slots'].get(g, '-')})")
        for d in r["details"]:
            lines.append(f"      CHEVAUCHEMENT {d}")
    total = sum(r["game_overlaps"] + r["our_overlaps"] for r in rows)
    lines += ["", f"Chevauchements au total : {total}"]
    (out / "formats.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"rows": rows, "overlaps": total}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.overlay_formats_sheet", description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="overlay_formats_out")
    ap.add_argument("--no-zoom", action="store_true")
    a = ap.parse_args(argv)
    res = run(Path(a.out), zoom=not a.no_zoom)
    sys.stdout.write((Path(a.out) / "formats.txt").read_text(encoding="utf-8"))
    return 0 if res["overlaps"] == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
