"""Publish layout of a build (see packaging/bootstrap.py):

    python packaging/make_release.py --launcher dist/TreeAICoach.exe --app dist/TreeAICoach-app \\
        --release release --notes "2.14.0 : ..."          (--version: default treeaicoach.__version__)

writes ``release/TreeAICoach.exe`` (the launcher), ``release/app/<version>/part-NN.zip`` (independent zips,
each <= 90 MB), ``release/app/manifest.json`` and ``release/version.json`` (describes the launcher, so
the in-app updater of 2.13 and older downloads the launcher, which then installs the app). Other
``release/app/<version>`` folders are removed. Standard library only, deterministic output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHUNK_LIMIT = 90_000_000            # GitHub refuses files > 100 MB
LEVEL = 9
APP_EXE = "TreeAICoach-app.exe"
DATE = (2020, 1, 1, 0, 0, 0)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _deflated_size(p: Path) -> int:
    c = zlib.compressobj(LEVEL, zlib.DEFLATED, -15)
    n = 0
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            n += len(c.compress(chunk))
    return n + len(c.flush())


def plan_chunks(app_dir: Path, limit: int = CHUNK_LIMIT) -> list[list[tuple[Path, str]]]:
    """Files in path order, packed greedily into groups whose zip stays under ``limit``."""
    files = sorted((p for p in app_dir.rglob("*") if p.is_file()), key=lambda p: p.relative_to(app_dir).as_posix())
    groups: list[list[tuple[Path, str]]] = [[]]
    used = 0
    for p in files:
        name = p.relative_to(app_dir).as_posix()
        cost = _deflated_size(p) + 2 * len(name.encode()) + 200        # data + local/central headers
        budget = limit * 0.98 - 1000                                     # estimate margin
        if cost > budget:
            raise SystemExit(f"[make_release] {name} ne tient pas dans un morceau de {limit} octets")
        if used + cost > budget and groups[-1]:
            groups.append([])
            used = 0
        groups[-1].append((p, name))
        used += cost
    return groups


def write_zip(group: list[tuple[Path, str]], out: Path) -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=LEVEL) as z:
        for p, name in group:
            info = zipfile.ZipInfo(name, date_time=DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            for attr in ("compress_level", "_compresslevel"):   # 3.13+ / older (open() ignores compresslevel=)
                try:
                    setattr(info, attr, LEVEL)
                except AttributeError:
                    pass
            info.external_attr = 0o644 << 16
            with p.open("rb") as src, z.open(info, "w") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)


def chunk_sha(version: str, chunks: list[dict]) -> str:
    """Identity of a build (same formula as bootstrap.manifest_sha)."""
    return hashlib.sha256((version + "\n" + "".join(c["sha256"] for c in chunks)).encode("ascii")).hexdigest()


def build(launcher: Path, app_dir: Path, release: Path, version: str, notes: str = "",
          limit: int = CHUNK_LIMIT) -> dict:
    if not (app_dir / APP_EXE).is_file():
        raise SystemExit(f"[make_release] {app_dir / APP_EXE} introuvable (build one-dir manquant ?)")
    if not re.fullmatch(r"\d+(\.\d+){1,3}", version):
        raise SystemExit(f"[make_release] version invalide : {version!r}")
    app_root = release / "app"
    vdir = app_root / version
    shutil.rmtree(vdir, ignore_errors=True)
    vdir.mkdir(parents=True)
    chunks = []
    groups = plan_chunks(app_dir, limit)
    for i, group in enumerate(groups, 1):
        out = vdir / f"part-{i:02d}.zip"
        write_zip(group, out)
        size = out.stat().st_size
        if size > limit:
            raise SystemExit(f"[make_release] {out} dépasse {limit} octets")
        chunks.append({"name": f"{version}/{out.name}", "size": size, "sha256": sha256(out), "files": len(group)})
    exe_out = release / "TreeAICoach.exe"
    if launcher.resolve() != exe_out.resolve():
        shutil.copyfile(launcher, exe_out)
    lau = {"sha256": sha256(exe_out), "size": exe_out.stat().st_size}
    manifest = {"version": version, "exe": APP_EXE, "sha256": chunk_sha(version, chunks), "chunks": chunks,
                "total_size": sum(c["size"] for c in chunks), "launcher": lau, "notes": notes[:2000]}
    (app_root / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    (release / "version.json").write_text(json.dumps({"version": version, **lau, "notes": notes[:2000]},
                                                     indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for d in app_root.iterdir():
        if d.is_dir() and d.name != version:
            shutil.rmtree(d)
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--launcher", required=True, type=Path)
    ap.add_argument("--app", required=True, type=Path, help="one-dir folder (contains TreeAICoach-app.exe)")
    ap.add_argument("--release", required=True, type=Path)
    ap.add_argument("--version", default=None)
    ap.add_argument("--notes", default="")
    a = ap.parse_args(argv)
    if not a.version:
        text = (HERE.parent / "treeaicoach" / "__init__.py").read_text(encoding="utf-8")
        a.version = re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)
    m = build(a.launcher, a.app, a.release, a.version, a.notes)
    print(f"release ok: {a.version}, launcher {m['launcher']['size'] / 1e6:.1f} MB, "
          f"{len(m['chunks'])} chunks {[round(c['size'] / 1e6, 1) for c in m['chunks']]} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
