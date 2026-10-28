"""« Envoyer à TreeAI »: one small zip (<= ~10 Mo) with what helps improve TreeAI.

:func:`build_export` packs, from the user data directory:

* ``games/*.json`` + ``games/truth/*.truth.json``: the last :data:`MAX_GAMES` game records and their
  League Client truth, ids removed (riot id, summoner name, puuid), deflated;
* ``collect/*``: the minimap captures of those games, the useful ones first (roster disagreement, low
  confidence, stacks, fights, the shop screenshot), at most :data:`MAX_CAPTURES` / :data:`CAPTURE_BYTES`;
* ``reports/*.zip``: the last analysis reports (Ctrl+F8), ``diagnostics/*.zip``: the last diagnostics,
  while the zip stays under :data:`BUDGET`;
* ``logs/*.txt``: only WARNING / ERROR lines, voice / transcript / Discord lines and the last
  :data:`LOG_TAIL` lines (paths and Riot IDs masked), never the whole 8k-line logs;
* ``info.json``: app version, PC (CPU, RAM, GPU, Windows build, screen + scale, LoL window), settings
  (no key / token / name), voice engine state, performance per thread, storage use, game running or not;
* ``LISEZMOI.txt``: what is inside, in French.

Written to ``user_data_dir()/exports``. Pure file work, meant for a worker thread; never raises
(``(None, {"error": ...})`` on failure).
"""

from __future__ import annotations

import json
import logging
import re
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

EXPORT_DIR_NAME = "exports"
MAX_GAMES = 3
MAX_DIAGNOSTICS = 2
MAX_REPORTS = 3
MAX_CAPTURES = 60
CAPTURE_BYTES = 4 * 1024 * 1024
BUDGET = 10 * 1024 * 1024          # optional parts (reports, diagnostics) only while under this
LOG_TAIL = 300
LOG_MAX_LINES = 1500               # per log file, after filtering
FORMAT = 2
#: Already-compressed members are stored, not deflated again (faster, same size).
_STORED = {".png", ".jpg", ".jpeg", ".zip", ".webp"}
#: capture file name tag -> priority (lower first); untagged (legacy PNG every 30 s) last
_CAPTURE_PRIO = {"roster": 0, "lowconf": 1, "stack": 2, "fight": 3, "shop": 4, "full": 6}
_ID_KEYS = re.compile(r"(?i)^(riot_?id|summoner_?name|summoner_?id|puuid|game_?name|tag_?line|account_?id|"
                      r"player_?id|display_?name|riotIdGameName|riotIdTagLine|summonerName|summonerId)$")
_RIOT_ID = re.compile(r"[^\s#\"]{2,16}#[A-Za-z0-9]{2,5}\b")
_LOG_KEEP = re.compile(r"(?i)\b(WARNING|ERROR|CRITICAL)\b|voice|vocal|transcri|whisper|vosk|wake|discord|stt|tts|"
                       r"Traceback|Exception")


def _files(folder: Path, pattern: str) -> list[Path]:
    try:
        return sorted(p for p in folder.glob(pattern) if p.is_file())
    except OSError:
        return []


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def strip_ids(x: Any, depth: int = 0) -> Any:
    """Copy of a record / truth without player ids (keys dropped, ``Name#TAG`` masked)."""
    if depth > 40:
        return None
    if isinstance(x, dict):
        return {k: strip_ids(v, depth + 1) for k, v in x.items() if not _ID_KEYS.match(str(k))}
    if isinstance(x, list):
        return [strip_ids(v, depth + 1) for v in x]
    if isinstance(x, str) and "#" in x:
        return _RIOT_ID.sub("<joueur>", x)
    return x


def _json_bytes(p: Path) -> bytes:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return json.dumps(strip_ids(data), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (OSError, ValueError):
        return b""


def _capture_prio(p: Path) -> int:
    stem = p.stem.rsplit("_", 1)[-1]
    return _CAPTURE_PRIO.get(stem, 5)


def _game_start(rec: Path) -> float:
    """Wall-clock start of a record from its name ``YYYY-MM-DD_HHMM_...`` (mtime - 2 h if unparsable)."""
    m = re.match(r"(\d{4}-\d{2}-\d{2}_\d{4})", rec.name)
    if m:
        try:
            return time.mktime(time.strptime(m.group(1), "%Y-%m-%d_%H%M"))
        except (ValueError, OverflowError):
            pass
    return _mtime(rec) - 7200


def collect_sources(base: Path, max_games: int = MAX_GAMES) -> dict[str, list[tuple[Path, str]]]:
    """``{kind: [(file, name in the zip)]}``: games, truth, captures, reports, diagnostics."""
    from treeaicoach.storage import game_records  # noqa: PLC0415

    games_dir = base / "games"
    recs = game_records(games_dir)[-max_games:] if max_games > 0 else []
    truths = _files(games_dir / "truth", "*.truth.json")
    truth = []
    for r in recs:
        stem = r.name[:-len(".json")]
        truth += [t for t in truths if t.name.endswith(stem + ".truth.json")][:1]
    caps = _files(base / "collect", "*.jpg") + _files(base / "collect", "*.png")
    if recs:
        since = _game_start(recs[0]) - 600
        caps = [c for c in caps if _mtime(c) >= since]
    caps.sort(key=lambda c: (_capture_prio(c), -_mtime(c)))
    picked, total = [], 0
    for c in caps:
        sz = c.stat().st_size if c.exists() else 0
        if len(picked) >= MAX_CAPTURES or total + sz > CAPTURE_BYTES:
            continue
        picked.append(c)
        total += sz
    picked.sort(key=lambda c: c.name)
    newest = lambda d, pat, n: sorted(_files(d, pat), key=_mtime)[-n:] if n > 0 else []  # noqa: E731
    return {
        "games": [(p, f"games/{p.name}") for p in recs],
        "truth": [(p, f"games/truth/{p.name}") for p in truth],
        "captures": [(p, f"collect/{p.name}") for p in picked],
        "reports": [(p, f"reports/{p.name}") for p in newest(base / "reports", "*.zip", MAX_REPORTS)][::-1],
        "diagnostics": [(p, f"diagnostics/{p.name}") for p in newest(base / "diagnostics", "*.zip",
                                                                      MAX_DIAGNOSTICS)][::-1],
    }


def filter_log(text: str, tail: int = LOG_TAIL, max_lines: int = LOG_MAX_LINES) -> str:
    """Warnings / errors (+ their traceback), voice / transcript / Discord lines, and the last ``tail``
    lines; paths and Riot IDs masked."""
    lines = text.splitlines()
    keep: list[str] = []
    in_tb = False
    cut = max(0, len(lines) - tail)
    for i, ln in enumerate(lines):
        if i >= cut:
            keep.append(ln)
            continue
        if _LOG_KEEP.search(ln):
            keep.append(ln)
            in_tb = "ERROR" in ln or "Traceback" in ln or "CRITICAL" in ln
        elif in_tb and (ln[:1] in (" ", "\t") or ln.startswith(("Traceback", "  File"))):
            keep.append(ln)
        else:
            in_tb = False
    if len(keep) > max_lines:
        keep = [f"[... {len(keep) - max_lines} lignes plus anciennes coupées ...]"] + keep[-max_lines:]
    out = "\n".join(keep)
    try:
        from treeaicoach.diag import _mask_paths  # noqa: PLC0415

        out = _mask_paths(out)
    except Exception:
        pass
    return _RIOT_ID.sub("<joueur>", out) + "\n"


def _logs(base: Path) -> list[tuple[str, bytes]]:
    out = []
    for p in sorted(_files(base / "logs", "*.log*"), key=_mtime, reverse=True)[:3]:
        try:
            if p.stat().st_size == 0:
                continue
            text = p.read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        out.append((f"logs/{p.name}.txt", filter_log(text).encode("utf-8")))
    return out


def info(engine: Any = None, base: Path | None = None) -> dict[str, Any]:
    """``info.json``: app, PC, settings, voice, perf, storage, game running. Never raises."""
    out: dict[str, Any] = {"format": FORMAT, "created": time.strftime("%Y-%m-%d %H:%M:%S")}

    def part(name: str, fn: Callable[[], Any]) -> None:
        try:
            out[name] = fn()
        except Exception as exc:
            out[name] = {"error": type(exc).__name__}

    from treeaicoach import analysis_report as ar  # noqa: PLC0415

    def pc() -> dict:
        from treeaicoach.diag import pc_info  # noqa: PLC0415
        return pc_info()

    def settings() -> dict:
        cfg = getattr(engine, "_cfg", None) if engine is not None else None
        if cfg is None:
            from treeaicoach.config import load_config  # noqa: PLC0415
            cfg = load_config()
        return ar.settings_dict(cfg)

    def storage_use() -> dict:
        from treeaicoach import storage  # noqa: PLC0415
        return {k: {"bytes": v["bytes"], "files": v["files"]} for k, v in storage.usage(base).items()}

    def game() -> dict:
        g = getattr(engine, "_game", None) if engine is not None else None
        return {"running": g is not None, "game_time": getattr(g, "game_time", None) if g is not None else None,
                "engine": engine is not None}

    def selfcheck() -> Any:
        h = engine.health() if engine is not None and callable(getattr(engine, "health", None)) else {}
        return {k: (h or {}).get(k) for k in ("performance", "capture", "selfcheck")}

    part("app", lambda: {"version": __import__("treeaicoach").__version__})
    part("game", game)
    part("pc", pc)
    part("settings", settings)
    part("voice", lambda: ar.voice_status(engine))
    part("perf", lambda: ar.perf_summary())
    part("health", selfcheck)
    part("storage", storage_use)
    part("last_game", lambda: ar.last_game_summary(None if base is None else Path(base) / "games"))
    return ar.scrub(out)


def _readme(st: dict, inf: dict) -> str:
    running = bool((inf.get("game") or {}).get("running"))
    lines = ["Envoi TreeAI Coach", "",
             "Partie en cours au moment de l'envoi." if running else
             "AUCUNE PARTIE EN COURS au moment de l'envoi (les données viennent des parties enregistrées).", "",
             f"- games/ : {st['games']} parties (les plus récentes) et {st['truth']} vérités du client LoL, sans pseudos",
             f"- collect/ : {st['captures']} captures de minimap choisies (désaccords, doutes, combats, boutique)",
             f"- reports/ : {st['reports']} rapports d'analyse (Ctrl+F8) ; diagnostics/ : {st['diagnostics']}",
             f"- logs/ : {st['logs']} journaux filtrés (avertissements, erreurs, voix, Discord, {LOG_TAIL} dernières "
             "lignes)",
             "- info.json : version, PC, réglages (sans clé ni pseudo), voix, performances, stockage", "",
             "Lecture : python tools/analyze_report.py <ce zip>"]
    return "\n".join(lines) + "\n"


def build_export(base: Path | None = None, dest_dir: Path | None = None,
                 progress: Callable[[int, int], None] | None = None, engine: Any = None,
                 max_games: int = MAX_GAMES, budget: int = BUDGET) -> tuple[Path | None, dict[str, Any]]:
    """Write ``TreeAI_envoi_<date>_<time>.zip`` and return ``(path, stats)``; ``stats`` has the
    count per kind, ``bytes`` (zip size) and ``error`` when nothing was written."""
    stats: dict[str, Any] = {"captures": 0, "games": 0, "truth": 0, "reports": 0, "diagnostics": 0, "logs": 0,
                             "skipped": 0, "bytes": 0}
    try:
        if base is None:
            from treeaicoach.paths import user_data_dir

            base = Path(user_data_dir())
        base = Path(base)
        sources = collect_sources(base, max_games)
        logs = _logs(base)
        if not any(sources.values()) and not logs:
            stats["error"] = "aucune capture ni partie à envoyer"
            return None, stats
        dest = Path(dest_dir) if dest_dir is not None else base / EXPORT_DIR_NAME
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / time.strftime("TreeAI_envoi_%Y%m%d_%H%M%S.zip")
        tmp = path.with_suffix(".zip.part")
        todo = [(kind, src, arc) for kind in ("games", "truth", "captures", "reports", "diagnostics")
                for src, arc in sources[kind]]
        inf = info(engine, base)
        with open(tmp, "wb") as fh, zipfile.ZipFile(fh, "w", compression=zipfile.ZIP_DEFLATED,
                                                    allowZip64=True) as zf:
            for arc, data in logs:
                zf.writestr(arc, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
                stats["logs"] += 1
            for i, (kind, src, arc) in enumerate(todo):
                try:
                    if kind in ("reports", "diagnostics") and fh.tell() + src.stat().st_size > budget:
                        stats["skipped"] += 1
                        continue
                    if kind in ("games", "truth"):
                        data = _json_bytes(src)
                        if not data:
                            continue
                        zf.writestr(arc, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
                    else:
                        comp = zipfile.ZIP_STORED if src.suffix.lower() in _STORED else zipfile.ZIP_DEFLATED
                        zf.write(src, arc, compress_type=comp)
                    stats[kind] += 1
                except OSError:
                    log.warning("Export: %s skipped", src, exc_info=True)
                if progress is not None and (i % 20 == 0 or i == len(todo) - 1):
                    progress(i + 1, len(todo))
            zf.writestr("info.json", json.dumps(inf, ensure_ascii=False, indent=1))
            zf.writestr("LISEZMOI.txt", _readme(stats, inf))
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
