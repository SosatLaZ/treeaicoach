"""Build release/TreeAICoach.exe = bootstrap stub + zip of the one-dir app + trailer (see bootstrap.py).

    python packaging/make_bundle.py --stub dist/TreeAICoachBoot.exe --app dist/TreeAICoach \\
        --out dist/TreeAICoach.exe            (--version: default treeaicoach.__version__)

Runs with any Python 3.9+ (Linux or Windows): standard library only. The zip is deterministic
(sorted names, fixed timestamps), deflate level 9.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_BYTES = 98 * 1024 * 1024          # GitHub refuses files > 100 MB; keep a margin


def _boot():
    spec = importlib.util.spec_from_file_location("tac_bootstrap", HERE / "bootstrap.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def zip_dir(app_dir: Path, out_zip: Path, level: int = 9) -> int:
    files = sorted(p for p in app_dir.rglob("*") if p.is_file())
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=level) as z:
        for p in files:
            info = zipfile.ZipInfo(p.relative_to(app_dir).as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with p.open("rb") as src, z.open(info, "w") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    return len(files)


def build(stub: Path, app_dir: Path, version: str, out: Path, exe_name: str = "TreeAICoach.exe",
          level: int = 9, max_bytes: int = MAX_BYTES) -> dict:
    boot = _boot()
    if not (app_dir / exe_name).is_file():
        raise SystemExit(f"[make_bundle] {app_dir / exe_name} introuvable (build one-dir manquant ?)")
    with tempfile.TemporaryDirectory() as td:
        zp = Path(td) / "payload.zip"
        n = zip_dir(app_dir, zp, level)
        h = hashlib.sha256()
        with zp.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        stub_size = stub.stat().st_size
        meta = {"version": version, "sha256": h.hexdigest(), "offset": stub_size,
                "size": zp.stat().st_size, "exe": exe_name, "files": n}
        tmp_out = out.with_name(out.name + ".part")
        with tmp_out.open("wb") as dst:
            with stub.open("rb") as src:
                shutil.copyfileobj(src, dst, 1 << 20)
            with zp.open("rb") as src:
                shutil.copyfileobj(src, dst, 1 << 20)
            dst.write(boot.make_trailer(meta))
        os.replace(tmp_out, out)
    size = out.stat().st_size
    if boot.read_trailer(str(out)) != meta:
        raise SystemExit("[make_bundle] trailer illisible après écriture")
    if size > max_bytes:
        raise SystemExit(f"[make_bundle] {out} fait {size / 1e6:.1f} Mo > limite {max_bytes / 1e6:.1f} Mo")
    meta["total"] = size
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stub", required=True, type=Path)
    ap.add_argument("--app", required=True, type=Path, help="one-dir folder (contains TreeAICoach.exe)")
    ap.add_argument("--version", default=None, help="default: treeaicoach/__init__.py __version__")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--level", type=int, default=9)
    a = ap.parse_args(argv)
    if not a.version:
        import re

        text = (HERE.parent / "treeaicoach" / "__init__.py").read_text(encoding="utf-8")
        a.version = re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)
    meta = build(a.stub, a.app, a.version, a.out, level=a.level)
    print(f"bundle ok: {a.out} {meta['total'] / 1e6:.1f} MB (stub {meta['offset'] / 1e6:.1f} MB, "
          f"payload {meta['size'] / 1e6:.1f} MB, {meta['files']} files, sha {meta['sha256'][:12]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
