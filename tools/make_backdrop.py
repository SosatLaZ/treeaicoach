"""Write the launcher's black-hole background (docs/DESIGN.md, "Lanceur").

    python tools/make_backdrop.py [--out treeaicoach/assets/ui/backdrop.png]

Renders :func:`treeaicoach.ui_backdrop.render_rgb` at 1600 x 1000 (the slow-varying light only:
the stars are drawn by the window at device resolution) and saves an optimised PNG (~120 KB).
Run it again after changing the drawing in ``ui_backdrop.py``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    from PIL import Image

    from treeaicoach import ui_backdrop as B

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "treeaicoach" / "assets" / Path(*B.ASSET)))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = B.ASSET_SIZE
    Image.fromarray(B.render_rgb(w, h)).save(out, optimize=True)
    print(f"{out} : {w} x {h}, {out.stat().st_size // 1024} Ko")
    return 0


if __name__ == "__main__":
    sys.exit(main())
