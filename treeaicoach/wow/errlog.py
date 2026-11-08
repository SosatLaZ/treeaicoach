"""« TreeAI-erreurs.txt » : the WoW addon's diagnostics as a plain French text file.

A WoW addon cannot write files. In game, ``/tai erreurs`` (alias ``/tai export``) gathers everything into
``TreeAIDB.diag.export``; the game saves TreeAIDB at /reload or logout, and every time TreeAI reads that
SavedVariables file (WoW mode / WoW page) this module writes ``TreeAI-erreurs.txt`` into the user's data folder
(``%APPDATA%\\TreeAICoach``) plus a copy in its ``exports`` folder when it exists. Even without the command the
file lists what the addon keeps by itself (``diag.errors``, ``diag.blocked``, ``diag.selftest``, ``diag.macros``).

Pure stdlib, never raises, rewrites only when the content changes.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

FILE_NAME = "TreeAI-erreurs.txt"
MAX_ITEMS = 60
COMMAND = "/tai erreurs"

_lock = threading.Lock()
_last_sig: str | None = None


def _seq(v: Any) -> list:
    """A Lua array parsed as list or {1: …, 2: …} dict -> list (in order)."""
    if isinstance(v, list):
        return [x for x in v if x is not None]
    if isinstance(v, dict):
        keys = sorted(k for k in v if isinstance(k, int))
        return [v[k] for k in keys]
    return []


def _d(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _s(v: Any, n: int = 300) -> str:
    s = " ".join(str("" if v is None else v).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _when(t: Any) -> str:
    t = _i(t)
    if t <= 0:
        return "?"
    try:
        return time.strftime("%d/%m/%Y %H:%M", time.localtime(t))
    except (OverflowError, OSError, ValueError):
        return "?"


def _diag(db: Any) -> dict:
    return _d(_d(db).get("diag"))


def build_text(db: Any, now: float | None = None) -> str:
    """The report (French, newest first). ``db`` = parsed TreeAIDB."""
    d = _diag(db)
    errs = [e for e in _seq(d.get("errors")) if isinstance(e, dict)]
    blocked = [e for e in _seq(d.get("blocked")) if isinstance(e, dict)]
    st = _d(d.get("selftest"))
    mac = _d(d.get("macros"))
    ex = _d(d.get("export"))
    out: list[str] = []
    out.append("TreeAI — erreurs WoW")
    out.append(f"Écrit par TreeAI Coach le {_when(now if now is not None else time.time())}.")
    out.append(f"Pour mettre à jour : en jeu, tape {COMMAND} puis /reload.")
    out.append("")
    n_err = sum(max(1, _i(e.get("n"), 1)) for e in errs)
    n_blk = sum(max(1, _i(e.get("n"), 1)) for e in blocked)
    out.append("== Résumé ==")
    out.append(f"Erreurs Lua : {len(errs)} différentes, {n_err} au total")
    out.append(f"Actions bloquées par le jeu : {n_blk}")
    if st:
        out.append(f"Autotest du {_when(st.get('t'))} : {_i(st.get('ok'))} ok, {_i(st.get('warn'))} à vérifier, "
                   f"{_i(st.get('ko'))} en échec (TreeAI {_s(st.get('version'), 20)}, client {_s(st.get('build'), 30)})")
    else:
        out.append("Autotest : jamais lancé (/tai test)")
    if ex:
        out.append(f"Rapport de l'addon : {_when(ex.get('at'))} (TreeAI {_s(ex.get('version'), 20)}, "
                   f"client {_s(ex.get('build'), 30)}, interface {_s(ex.get('interface'), 12)})")
    else:
        out.append(f"Rapport de l'addon : pas encore fait ({COMMAND} puis /reload)")
    out.append("")

    out.append("== Erreurs Lua (les plus récentes d'abord) ==")
    if not errs:
        out.append("Aucune.")
    for e in sorted(errs, key=lambda e: _i(e.get("t")), reverse=True)[:MAX_ITEMS]:
        extra = " (en combat)" if e.get("combat") else ""
        ctx = f" [{_s(e.get('ctx'), 60)}]" if e.get("ctx") else ""
        out.append(f"{_when(e.get('t'))}  {_s(e.get('module'), 30)} ×{max(1, _i(e.get('n'), 1))}{extra}{ctx}")
        out.append(f"    {_s(e.get('msg'), 400)}")
    out.append("")

    out.append("== Actions bloquées (fonction, pendant quoi, combien) ==")
    if not blocked:
        out.append("Aucune.")
    groups: dict[tuple[str, str], dict] = {}
    for e in blocked:
        key = (_s(e.get("func"), 80), _s(e.get("kind"), 20))
        g = groups.setdefault(key, {"n": 0, "t": 0, "ctx": set(), "combat": False})
        g["n"] += max(1, _i(e.get("n"), 1))
        g["t"] = max(g["t"], _i(e.get("t")))
        g["ctx"].add(_s(e.get("ctx") or "?", 60))
        g["combat"] = g["combat"] or bool(e.get("combat"))
    for (func, kind), g in sorted(groups.items(), key=lambda kv: kv[1]["t"], reverse=True):
        extra = " (en combat)" if g["combat"] else ""
        out.append(f"{_when(g['t'])}  {func} ({kind}) ×{g['n']}{extra} — pendant : {', '.join(sorted(g['ctx']))}")
    out.append("")

    if st:
        out.append("== Autotest : points à revoir ==")
        bad = [it for it in _seq(st.get("items")) if isinstance(it, dict) and it.get("status") in ("ko", "warn")]
        if not bad:
            out.append("Tout est bon.")
        for it in bad:
            mark = "ÉCHEC" if it.get("status") == "ko" else "à vérifier"
            out.append(f"{mark} · {_s(it.get('label') or it.get('id'), 60)} : {_s(it.get('detail'), 200)}")
        out.append("")
    if mac:
        out.append("== Macros ==")
        out.append(f"Diagnostic du {_when(mac.get('t'))} : {_s(mac.get('ok'), 8)} ok, {_s(mac.get('ko'), 8)} ko")
        for k in sorted(k for k in mac if isinstance(k, str) and k not in ("t", "ok", "ko")):
            v = mac[k]
            if isinstance(v, (dict, list)):
                v = ", ".join(_s(x, 60) for x in (_seq(v) or [f"{a}={b}" for a, b in _d(v).items()]))
            out.append(f"  {k} : {_s(v, 200)}")
        out.append("")
    lines = [_s(x, 400) for x in _seq(ex.get("lines"))]
    if lines:
        out.append(f"== Rapport complet de l'addon ({COMMAND} du {_when(ex.get('at'))}) ==")
        out.extend(lines[:250])
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def default_dirs() -> list[Path]:
    """The user's TreeAI data folder, plus its ``exports`` folder when it exists."""
    try:
        from treeaicoach.paths import user_data_dir  # noqa: PLC0415

        base = Path(user_data_dir())
    except Exception:
        log.debug("errlog: no data dir", exc_info=True)
        return []
    dirs = [base]
    try:
        from treeaicoach.export import EXPORT_DIR_NAME  # noqa: PLC0415

        exp = base / EXPORT_DIR_NAME
        if exp.is_dir():
            dirs.append(exp)
    except Exception:
        log.debug("errlog: no exports dir", exc_info=True)
    return dirs


def report_path(dirs: list[Path] | None = None) -> Path | None:
    dirs = default_dirs() if dirs is None else dirs
    return (Path(dirs[0]) / FILE_NAME) if dirs else None


def _has_data(db: Any) -> bool:
    """Only an addon save that carries diagnostics (TreeAIDB.diag) makes a file."""
    return isinstance(db, dict) and isinstance(db.get("diag"), dict)


def write_report(db: Any, dirs: list[Path] | None = None, force: bool = False) -> Path | None:
    """Write TreeAI-erreurs.txt when the diagnostics changed. Returns the main path written (None otherwise)."""
    global _last_sig
    try:
        if not _has_data(db):
            return None
        dirs = default_dirs() if dirs is None else [Path(p) for p in dirs]
        if not dirs:
            return None
        body = build_text(db)
        # signature without the « Écrit … le » line: same diagnostics = no rewrite
        sig = hashlib.sha1(("\n".join(body.splitlines()[2:]) + "|" + "|".join(map(str, dirs))).encode("utf-8")).hexdigest()
        main = dirs[0] / FILE_NAME
        with _lock:
            if not force and sig == _last_sig and main.is_file():
                return None
            written = None
            for d in dirs:
                try:
                    d.mkdir(parents=True, exist_ok=True)
                    p = d / FILE_NAME
                    tmp = p.with_suffix(".tmp")
                    tmp.write_text(body, encoding="utf-8")
                    os.replace(tmp, p)
                    written = written or p
                except OSError:
                    log.debug("errlog: cannot write in %s", d, exc_info=True)
            if written is not None:
                _last_sig = sig
            return written
    except Exception:
        log.debug("errlog: write failed", exc_info=True)
        return None


def read_report(dirs: list[Path] | None = None) -> str:
    p = report_path(dirs)
    try:
        return p.read_text(encoding="utf-8") if p is not None and p.is_file() else ""
    except OSError:
        return ""


def open_file(path: Path | str | None) -> bool:
    """Open the file with the system's text editor. Never raises."""
    try:
        if path is None or not Path(path).is_file():
            return False
        if sys.platform.startswith("win") and hasattr(os, "startfile"):
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
            return True
        import subprocess  # noqa: PLC0415

        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603
        return True
    except Exception:
        log.debug("errlog: open failed", exc_info=True)
        return False


def _reset() -> None:
    """Forget the last signature (tests)."""
    global _last_sig
    _last_sig = None
