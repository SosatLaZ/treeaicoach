"""What TreeAI keeps on disk, and the cleanup (Réglages > Stockage, and once at start-up).

:func:`usage` sums the user data folder per kind (captures, parties, rapports, diagnostics, journaux,
caches, modèles). :func:`plan_cleanup` lists what the cleanup would delete, :func:`run_cleanup`
deletes it. Rules (same for the button and the automatic cleanup at start-up):

* games: the last ``keep_games`` games stay complete (record + HTML report + LCU truth); older games
  lose their HTML report and their truth, the record itself (~200 Ko, read by the progress stats)
  stays;
* minimap captures, analysis reports, diagnostics, exports: older than ``keep_days`` days;
* logs: rotated / old log files older than ``keep_days`` days (never the active log);
* caches and downloaded models (voice, GPU runtime, voices...): never, unless ``include_models``.

Pure file work (worker thread). Never raises.
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DAY_S = 86400.0
KEEP_GAMES = 10
KEEP_DAYS = 7

#: kind -> (French label, sub-folders of the user data dir)
KINDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "captures": ("Captures de minimap", ("collect",)),
    "games": ("Parties et rapports d'après-partie", ("games",)),
    "reports": ("Rapports d'analyse et zips d'envoi", ("reports", "exports")),
    "diagnostics": ("Diagnostics", ("diagnostics",)),
    "logs": ("Journaux", ("logs",)),
    "caches": ("Caches (icônes, données du jeu, phrases)", ("cache", "ddragon", "updates")),
    "models": ("Modèles téléchargés (voix, GPU)", ()),
}
#: sub-folders of cache/ that hold downloaded models (counted as "models", never cleaned by default)
MODEL_SUBDIRS = ("voice", "gpu")
_ACTIVE_LOGS = {"treeaicoach.log", "faults.log"}


def _base(base: Path | None) -> Path:
    if base is not None:
        return Path(base)
    from treeaicoach.paths import user_data_dir  # noqa: PLC0415

    return Path(user_data_dir())


def _size(p: Path) -> int:
    try:
        if p.is_file():
            return p.stat().st_size
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        return 0


def _count(p: Path) -> int:
    try:
        return 1 if p.is_file() else sum(1 for f in p.rglob("*") if f.is_file())
    except OSError:
        return 0


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _children(d: Path) -> list[Path]:
    try:
        return sorted(d.iterdir()) if d.is_dir() else []
    except OSError:
        return []


def usage(base: Path | None = None) -> dict[str, dict[str, Any]]:
    """``{kind: {"label", "bytes", "files"}}`` for every kind of :data:`KINDS`."""
    out: dict[str, dict[str, Any]] = {}
    try:
        b = _base(base)
        for kind, (label, subs) in KINDS.items():
            n = sz = 0
            for sub in subs:
                for c in _children(b / sub):
                    if sub == "cache" and c.name in MODEL_SUBDIRS:
                        continue
                    sz += _size(c)
                    n += _count(c)
            if kind == "models":
                for m in MODEL_SUBDIRS:
                    sz += _size(b / "cache" / m)
                    n += _count(b / "cache" / m)
            out[kind] = {"label": label, "bytes": sz, "files": n}
    except Exception:
        log.debug("storage usage failed", exc_info=True)
    return out


@dataclass
class Plan:
    """What :func:`run_cleanup` deletes: ``items`` = ``[(path, bytes, kind)]``."""
    items: list[tuple[Path, int, str]] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        return sum(b for _p, b, _k in self.items)

    def by_kind(self) -> dict[str, tuple[int, int]]:
        """``{kind: (count, bytes)}``."""
        out: dict[str, tuple[int, int]] = {}
        for _p, b, k in self.items:
            n, s = out.get(k, (0, 0))
            out[k] = (n + 1, s + b)
        return out


def game_records(games: Path) -> list[Path]:
    """Finished game records (``games/*.json``), oldest first (not partial, not caches)."""
    recs = [p for p in _children(games) if p.is_file() and p.suffix == ".json"
            and not p.name.endswith(".partial.json") and not p.name.startswith(("progress_", "call_", "."))
            and p.name[:2].isdigit()]
    return sorted(recs, key=lambda p: (p.name, _mtime(p)))


def plan_cleanup(base: Path | None = None, keep_games: int = KEEP_GAMES, keep_days: float = KEEP_DAYS,
                 now: float | None = None, include_models: bool = False) -> Plan:
    plan = Plan()
    try:
        b = _base(base)
        now = time.time() if now is None else float(now)
        cutoff = now - max(0.0, float(keep_days)) * DAY_S
        seen: set[Path] = set()

        def add(p: Path, kind: str) -> None:
            if p not in seen:
                seen.add(p)
                plan.items.append((p, _size(p), kind))

        games = b / "games"
        recs = game_records(games)
        old = recs[:-int(keep_games)] if keep_games > 0 else recs
        for r in old:
            stem = r.name[:-len(".json")]
            for h in _children(games):
                if h.suffix == ".html" and (h.stem == stem or h.stem.startswith(stem + "_")):
                    add(h, "games")
            for t in _children(games / "truth"):
                if t.name.endswith(stem + ".truth.json"):
                    add(t, "games")
        for sub, kind in (("collect", "captures"), ("reports", "reports"), ("exports", "reports"),
                          ("diagnostics", "diagnostics")):
            for p in _children(b / sub):
                if _mtime(p) < cutoff:
                    add(p, kind)
        for p in _children(b / "logs"):
            if p.is_file() and p.name not in _ACTIVE_LOGS and _mtime(p) < cutoff:
                add(p, "logs")
        if include_models:
            for m in MODEL_SUBDIRS:
                if (b / "cache" / m).exists():
                    add(b / "cache" / m, "models")
    except Exception:
        log.debug("cleanup plan failed", exc_info=True)
    return plan


def run_cleanup(plan: Plan) -> tuple[int, int]:
    """Delete the plan's items; ``(files deleted, bytes freed)``. Files in use are skipped."""
    n = freed = 0
    for p, sz, _k in plan.items:
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
                if p.exists():
                    continue
            else:
                p.unlink()
            n += 1
            freed += sz
        except OSError:
            log.debug("cleanup: %s kept (in use?)", p)
    if n:
        log.info("Storage cleanup: %d items, %.1f MB freed", n, freed / 1e6)
    return n, freed


def cfg_rules(cfg: Any) -> dict[str, Any]:
    return {"keep_games": int(getattr(cfg, "storage_keep_games", KEEP_GAMES) or KEEP_GAMES),
            "keep_days": float(getattr(cfg, "storage_keep_days", KEEP_DAYS) or KEEP_DAYS)}


def auto_cleanup(cfg: Any, base: Path | None = None) -> tuple[int, int]:
    """Start-up cleanup (setting ``storage_auto_clean``, on by default). Never deletes models."""
    try:
        if not getattr(cfg, "storage_auto_clean", True):
            return 0, 0
        return run_cleanup(plan_cleanup(base, **cfg_rules(cfg)))
    except Exception:
        log.debug("auto cleanup failed", exc_info=True)
        return 0, 0


def size_label(n: int) -> str:
    """``0 Ko`` / ``734 Ko`` / ``12,3 Mo`` / ``1,2 Go`` (French)."""
    if n <= 0:
        return "0 Ko"
    if n < 1024 * 1024:
        return f"{max(1, round(n / 1024))} Ko"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} Mo".replace(".", ",")
    return f"{n / 1024 ** 3:.1f} Go".replace(".", ",")


def describe_plan(plan: Plan, keep_games: int = KEEP_GAMES) -> str:
    """French text of the confirmation dialog."""
    bk = plan.by_kind()
    if not bk:
        return "Rien à supprimer : tout est déjà rangé."
    lines = [f"• {KINDS[k][0]} : {n} fichier{'s' if n > 1 else ''} ({size_label(s)})" for k, (n, s) in bk.items()]
    return ("Seront supprimés :\n" + "\n".join(lines) + f"\n\nEspace libéré : {size_label(plan.bytes)}.\n"
            f"Les {keep_games} dernières parties restent complètes ; pour les plus anciennes seuls le rapport HTML et la "
            "vérité sont supprimés (tes statistiques restent).")


def usage_text(u: dict[str, dict[str, Any]]) -> str:
    parts = [f"{v['label'].split(' (')[0]} {size_label(int(v['bytes']))}" for v in u.values() if v.get("bytes")]
    total = sum(int(v.get("bytes") or 0) for v in u.values())
    return (f"Total {size_label(total)} : " + " · ".join(parts)) if parts else "Aucune donnée pour l'instant."


__all__ = ["usage", "plan_cleanup", "run_cleanup", "auto_cleanup", "describe_plan", "usage_text", "size_label",
           "Plan", "KINDS", "game_records"]
