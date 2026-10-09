"""export.build_export: one zip with the captures, game records, their truth and the last
diagnostics (users sent dozens of files one by one)."""

from __future__ import annotations

import zipfile
from pathlib import Path

from treeaicoach import export


def _touch(p: Path, data: bytes = b"x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_build_export_packs_everything_once(tmp_path: Path) -> None:
    base = tmp_path / "TreeAICoach"
    for i in range(3):
        _touch(base / "collect" / f"20261009_2000{i:02d}_{i:04d}.png", b"\x89PNG" + bytes(50))
    _touch(base / "games" / "2026-10-09_2006_Swain.json", b'{"schema": 1}')
    _touch(base / "games" / "truth" / "abc-2026-10-09_2006_Swain.truth.json", b"{}")
    for i in range(5):
        _touch(base / "diagnostics" / f"diag_{i}.zip", b"PK")
    _touch(base / "logs" / "treeai.log", b"secret-ish log")          # not packed
    seen: list[tuple[int, int]] = []
    path, st = export.build_export(base, progress=lambda a, b: seen.append((a, b)))
    assert path is not None and path.exists() and path.parent == base / "exports"
    assert (st["captures"], st["games"], st["truth"], st["diagnostics"]) == (3, 1, 1, export.MAX_DIAGNOSTICS)
    assert st["bytes"] == path.stat().st_size and seen and seen[-1][0] == seen[-1][1]
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        assert "games/2026-10-09_2006_Swain.json" in names
        assert "games/truth/abc-2026-10-09_2006_Swain.truth.json" in names
        assert sum(n.startswith("collect/") for n in names) == 3
        assert not any(n.startswith("logs/") for n in names)
        assert zf.getinfo("collect/20261009_200000_0000.png").compress_type == zipfile.ZIP_STORED
        assert zf.getinfo("games/2026-10-09_2006_Swain.json").compress_type == zipfile.ZIP_DEFLATED
    assert not list((base / "exports").glob("*.part"))


def test_build_export_nothing_to_send(tmp_path: Path) -> None:
    path, st = export.build_export(tmp_path / "empty")
    assert path is None and "aucune" in st["error"]


def test_size_label() -> None:
    assert export.size_label(10) == "1 Ko"
    assert export.size_label(5 * 1024 * 1024 + 300 * 1024) == "5,3 Mo"
