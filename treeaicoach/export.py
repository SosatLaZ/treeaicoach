"""One zip of everything that helps improve TreeAI, to send in a single file.

:func:`build_export` packs, from the user data directory:

* ``collect/*.png``: the minimap captures ("Collecter des captures de minimap");
* ``games/*.json``: the game records, and ``games/truth/*.truth.json``: their League Client truth
  (the capture file names carry the wall-clock time, the records the game start time, so the
  captures can be aligned with the true positions);
* ``diagnostics/*.zip``: the last :data:`MAX_DIAGNOSTICS` diagnostics (Ctrl+F8).

The zip is written to ``user_data_dir()/exports``. PNG and zip members are stored as they are
(already compressed), JSON files are deflated. Pure file work, meant for a worker thread; never
raises (``(None, {"error": ...})`` on failure).
"""

from __future__ import annotations

import logging
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

EXPORT_DIR_NAME = "exports"
MAX_DIAGNOSTICS = 3
#: Already-compressed members are stored, not deflated again (faster, same size).
_STORED = {".png", ".jpg", ".jpeg", ".zip", ".webp"}


def _files(folder: Path, pattern: str) -> list[Path]:
    try:
        return sorted(p for p in folder.glob(pattern) if p.is_file())
    except OSError:
        return []


def collect_sources(base: Path) -> dict[str, list[tuple[Path, str]]]:
    """``{kind: [(file, name in the zip)]}`` for every file :func:`build_export` packs."""
    games = base / "games"
    diags = sorted(_files(base / "diagnostics", "*.zip"), key=lambda p: p.stat().st_mtime)[-MAX_DIAGNOSTICS:]
    return {
        "captures": [(p, f"collect/{p.name}") for p in _files(base / "collect", "*.png")],
        "games": [(p, f"games/{p.name}") for p in _files(games, "*.json")],
        "truth": [(p, f"games/truth/{p.name}") for p in _files(games / "truth", "*.json")],
        "diagnostics": [(p, f"diagnostics/{p.name}") for p in diags],
    }


def build_export(base: Path | None = None, dest_dir: Path | None = None,
                 progress: Callable[[int, int], None] | None = None) -> tuple[Path | None, dict[str, Any]]:
    """Write ``TreeAI_envoi_<date>_<time>.zip`` and return ``(path, stats)``; ``stats`` has the
    count per kind, ``bytes`` (zip size) and ``error`` when nothing was written."""
    stats: dict[str, Any] = {"captures": 0, "games": 0, "truth": 0, "diagnostics": 0, "bytes": 0}
    try:
        if base is None:
            from treeaicoach.paths import user_data_dir

            base = Path(user_data_dir())
        sources = collect_sources(Path(base))
        todo = [(kind, src, arc) for kind, items in sources.items() for src, arc in items]
        if not todo:
            stats["error"] = "aucune capture ni partie à envoyer"
            return None, stats
        dest = Path(dest_dir) if dest_dir is not None else Path(base) / EXPORT_DIR_NAME
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / time.strftime("TreeAI_envoi_%Y%m%d_%H%M%S.zip")
        tmp = path.with_suffix(".zip.part")
        with zipfile.ZipFile(tmp, "w", allowZip64=True) as zf:
            for i, (kind, src, arc) in enumerate(todo):
                comp = zipfile.ZIP_STORED if src.suffix.lower() in _STORED else zipfile.ZIP_DEFLATED
                try:
                    zf.write(src, arc, compress_type=comp)
                    stats[kind] += 1
                except OSError:
                    log.warning("Export: %s skipped", src, exc_info=True)
                if progress is not None and (i % 50 == 0 or i == len(todo) - 1):
                    progress(i + 1, len(todo))
        tmp.replace(path)
        stats["bytes"] = path.stat().st_size
        log.info("Export written: %s (%s)", path, stats)
        return path, stats
    except Exception as exc:  # disk full, permissions...: reported, never raised
        log.exception("Export failed")
        stats["error"] = str(exc) or type(exc).__name__
        return None, stats


def size_label(n: int) -> str:
    """``734 Ko`` / ``12,3 Mo`` (French)."""
    if n < 1024 * 1024:
        return f"{max(1, round(n / 1024))} Ko"
    return f"{n / (1024 * 1024):.1f} Mo".replace(".", ",")
