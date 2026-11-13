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
NAV_MAX = 300
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


def _blocked_rows(d: dict) -> list[dict]:
    """Blocked actions as {func, kind, ctx, n, t, combat}: Core's real counters ``diag.blockedTop`` (« func|ctx » =
    {func, ctx, kind, n, combat = count, last}) when present; ``diag.blocked`` keeps only the last 20 events."""
    top = [e for e in (_d(d.get("blockedTop")).values()) if isinstance(e, dict)]
    if top:
        return [{"func": e.get("func"), "kind": e.get("kind"), "ctx": e.get("ctx"), "n": max(1, _i(e.get("n"), 1)),
                 "t": e.get("last"), "combat": _i(e.get("combat")) > 0} for e in top]
    return [e for e in _seq(d.get("blocked")) if isinstance(e, dict)]


def _blocked_total(d: dict, rows: list[dict]) -> int:
    return _i(d.get("blockedTotal")) or sum(max(1, _i(e.get("n"), 1)) for e in rows)


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _spike(e: dict, count: bool = False) -> str:
    """One slowness spike kept by the addon (``diag.spikes``): {m module, j job, ms, k "appel"|"image", c combat,
    p nameplates, g group size, t, n calls of the frame, b biggest call, x times seen}."""
    where = []
    if e.get("c"):
        where.append("en combat")
    if _i(e.get("p")) > 0:
        where.append(f"{_i(e.get('p'))} barres de nom")
    if _i(e.get("g")) > 1:
        where.append(f"groupe de {_i(e.get('g'))}")
    if e.get("k") == "image":
        what = (f"image entière {_f(e.get('ms')):.1f} ms ({_i(e.get('n'))} appels, le plus gros : "
                f"{_s(e.get('m'), 30)} · {_s(e.get('j'), 60)} {_f(e.get('b')):.1f} ms)")
    else:
        what = f"{_s(e.get('m'), 30)} · {_s(e.get('j'), 60)} : {_f(e.get('ms')):.1f} ms"
    if count and _i(e.get("x"), 1) > 1:
        what += f" ×{_i(e.get('x'), 1)}"
    return what + (f" ({', '.join(where)})" if where else "")


def _spike_lines(d: dict) -> list[str]:
    """« Pics de lenteur »: what TreeAI measured in the real game (5.0: a call over 8 ms, a frame over 12 ms)."""
    sp = _d(d.get("spikes"))
    if not sp:
        return []
    out = ["== Pics de lenteur (mesurés en jeu par TreeAI) =="]
    out.append(f"Depuis le {_when(sp.get('since'))} (TreeAI {_s(sp.get('ver'), 20)}) : {_i(sp.get('calls'))} appel(s) de plus "
               f"de 8 ms, {_i(sp.get('frames'))} image(s) de plus de 12 ms.")
    asleep = _d(sp.get("asleep"))
    if asleep:
        names = [f"{_s(k, 30)} ({'erreurs' if _d(v).get('why') == 'err' else 'trop lent'})" for k, v in sorted(asleep.items())]
        out.append("Mis en pause par le chien de garde : " + ", ".join(names))
    top = sorted((e for e in _seq(sp.get("top")) if isinstance(e, dict)), key=lambda e: -_f(e.get("ms")))
    if not top:
        out.append("Aucun pic relevé.")
    else:
        out.append("Les plus gros :")
        for i, e in enumerate(top[:30], 1):
            out.append(f"{i:2d}. {_spike(e, True)}")
        out.append("Les derniers :")
        for e in reversed([e for e in _seq(sp.get("last")) if isinstance(e, dict)][-30:]):
            out.append(f"{_when(e.get('t'))}  {_spike(e)}")
    out.append("")
    return out


def _api_lines(d: dict) -> list[str]:
    """« API du client »: which game functions exist on this client and the shape of what they return (5.0)."""
    api = _d(d.get("api"))
    lines = [_s(x, 400) for x in _seq(api.get("lines")) if isinstance(x, str)]
    if not lines:
        return []
    return [f"== API du client (sonde du {_when(api.get('t'))}) ==", *lines[:200], ""]


def build_text(db: Any, now: float | None = None) -> str:
    """The report (French, newest first). ``db`` = parsed TreeAIDB."""
    d = _diag(db)
    errs = [e for e in _seq(d.get("errors")) if isinstance(e, dict)]
    blocked = _blocked_rows(d)
    st = _d(d.get("selftest"))
    mac = _d(d.get("macros"))
    ex = _d(d.get("export"))
    out: list[str] = []
    out.append("TreeAI — erreurs WoW")
    out.append(f"Écrit par TreeAI Coach le {_when(now if now is not None else time.time())}.")
    out.append(f"Pour mettre à jour : en jeu, tape {COMMAND} puis /reload.")
    out.append("")
    n_err = sum(max(1, _i(e.get("n"), 1)) for e in errs)
    n_blk = _blocked_total(d, blocked)
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
    out.extend(_spike_lines(d))
    out.extend(_api_lines(d))
    nav = [_s(x, 300) for x in _seq(_d(d.get("nav")).get("lines")) if isinstance(x, str)]
    if nav:
        # ZONEFIX (4.0.1): the compass / route decisions the addon keeps by itself (ring of the last 300, ≤ 30 KB)
        out.append(f"== Trajet / boussole ({len(nav)} évènements, les plus récents d'abord) ==")
        out.extend(reversed(nav[-NAV_MAX:]))
        out.append("")
    lines = [_s(x, 400) for x in _seq(ex.get("lines"))]
    if lines:
        out.append(f"== Rapport complet de l'addon ({COMMAND} du {_when(ex.get('at'))}) ==")
        out.extend(lines[:250])
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def diag_lists(db: Any, limit: int = 40) -> dict:
    """The WoW page's « Erreurs » tab (pure, never raises): the most frequent first.

    ``{"errors": [(text, tooltip, n)], "blocked": [(text, tooltip, n)], "tests": [(text, tone)],
    "counts": {errors, errors_total, blocked}, "export": when /tai erreurs ran (epoch) or 0}``."""
    out: dict = {"errors": [], "blocked": [], "tests": [], "counts": {"errors": 0, "errors_total": 0, "blocked": 0},
                 "export": 0}
    try:
        d = _diag(db)
        merged: dict[tuple[str, str], dict] = {}
        for e in _seq(d.get("errors")):
            if not isinstance(e, dict):
                continue
            k = (_s(e.get("module"), 30), _s(e.get("msg"), 400))
            g = merged.setdefault(k, {"n": 0, "t": 0, "combat": False, "ctx": ""})
            g["n"] += max(1, _i(e.get("n"), 1))
            g["t"] = max(g["t"], _i(e.get("t")))
            g["combat"] = g["combat"] or bool(e.get("combat"))
            g["ctx"] = g["ctx"] or _s(e.get("ctx"), 60)
        for (mod, msg), g in sorted(merged.items(), key=lambda kv: (-kv[1]["n"], -kv[1]["t"]))[:limit]:
            text = f"×{g['n']}  {mod or '?'} : {_s(msg, 140)}"
            head = f"{mod} · dernière fois le {_when(g['t'])}" + (" (en combat)" if g["combat"] else "")
            tip = "\n".join(x for x in (head, f"pendant : {g['ctx']}" if g["ctx"] else "", msg) if x)
            out["errors"].append((text, tip, g["n"]))
        blocked: dict[tuple[str, str], dict] = {}
        rows = _blocked_rows(d)
        for e in rows:
            k = (_s(e.get("func"), 80), _s(e.get("kind"), 20))
            g = blocked.setdefault(k, {"n": 0, "t": 0, "ctx": set(), "combat": False})
            g["n"] += max(1, _i(e.get("n"), 1))
            g["t"] = max(g["t"], _i(e.get("t")))
            g["ctx"].add(_s(e.get("ctx") or "?", 60))
            g["combat"] = g["combat"] or bool(e.get("combat"))
        for (func, kind), g in sorted(blocked.items(), key=lambda kv: (-kv[1]["n"], -kv[1]["t"]))[:limit]:
            ctx = ", ".join(sorted(g["ctx"]))
            text = f"×{g['n']}  {func or '?'}" + (f" ({kind})" if kind else "") + (f" · pendant : {_s(ctx, 60)}" if ctx else "")
            tip = (f"{func} · dernière fois le {_when(g['t'])}" + (" (en combat)" if g["combat"] else "")
                   + (f"\npendant : {ctx}" if ctx else ""))
            out["blocked"].append((text, tip, g["n"]))
        st = _d(d.get("selftest"))
        for it in _seq(st.get("items")):
            if isinstance(it, dict) and it.get("status") in ("ko", "warn"):
                bad = it.get("status") == "ko"
                out["tests"].append((f"{'Échec' if bad else 'À vérifier'} · {_s(it.get('label') or it.get('id'), 60)} : "
                                     f"{_s(it.get('detail'), 160)}", "danger" if bad else "warning"))
        out["counts"] = {"errors": len(merged), "errors_total": sum(g["n"] for g in merged.values()),
                         "blocked": _blocked_total(d, rows) if rows else 0}
        out["export"] = _i(_d(d.get("export")).get("at"))
    except Exception:
        log.debug("errlog: diag lists failed", exc_info=True)
    return out


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
