"""Build wow_addon/TreeAI/Data/Macros.lua from treeaicoach/wow/macros (lint first).

Usage: python tools/wow_build_macros.py [--check]   (--check: fail if the file is out of date)
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from treeaicoach.wow.macros import export_lua, lint_all  # noqa: E402

OUT = ROOT / "wow_addon" / "TreeAI" / "Data" / "Macros.lua"


def main(argv: list[str]) -> int:
    errs = lint_all()
    if errs:
        print("\n".join(errs))
        print(f"{len(errs)} problème(s) de macro")
        return 1
    text = export_lua()
    if "--check" in argv:
        cur = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if cur != text:
            print(f"{OUT} n'est pas à jour : lancer python tools/wow_build_macros.py")
            return 1
        print("Macros.lua à jour")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"écrit {OUT.relative_to(ROOT)} ({len(text)} octets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
