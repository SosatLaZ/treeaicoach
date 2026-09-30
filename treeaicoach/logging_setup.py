"""Logging configuration: rotating log file (+ optional console) and last-resort crash hooks.

``setup_logging()`` is idempotent (calling it again only updates levels/paths, it never
duplicates handlers). The console handler is only added when ``sys.stderr`` exists: in a
windowed PyInstaller exe ``sys.stdout`` / ``sys.stderr`` are ``None``.

``install_excepthooks()`` logs uncaught exceptions of the main thread (``sys.excepthook``),
of other threads (``threading.excepthook``) and "unraisable" ones (``sys.unraisablehook``),
and optionally enables ``faulthandler`` into ``logs/faults.log`` to trace native crashes.
"""

from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
import platform
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import IO, Any

from treeaicoach import paths

log = logging.getLogger(__name__)

LOG_FILE_NAME = "treeaicoach.log"
FAULT_FILE_NAME = "faults.log"
MAX_BYTES = 1024 * 1024            # 1 MB per file
BACKUP_COUNT = 3                   # treeaicoach.log.1 .. .3
LOG_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-8s [%(threadName)s] %(name)s:%(lineno)d: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
ROLLOVER_RETRY_S = 60.0            # after a failed rotation (file locked), retry later
QUIET_LOGGERS: dict[str, int] = {"PIL": logging.INFO}   # chatty third-party loggers

_TAG = "_treeaicoach_role"         # attribute set on our handlers: "file" | "console"
_HOOK_TAG = "_treeaicoach_hook"    # attribute set on our exception hooks

_lock = threading.RLock()
_log_file: Path | None = None
_fault_file: IO[str] | None = None


class _SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler whose failed rotation (file locked on Windows) is not fatal."""

    _retry_after: float = 0.0

    def shouldRollover(self, record: logging.LogRecord) -> bool:  # noqa: N802 (stdlib name)
        if time.monotonic() < self._retry_after:
            return False
        return bool(super().shouldRollover(record))

    def doRollover(self) -> None:  # noqa: N802 (stdlib name)
        try:
            super().doRollover()
        except OSError:
            self._retry_after = time.monotonic() + ROLLOVER_RETRY_S
            if self.stream is None:
                try:
                    self.stream = self._open()
                except OSError:
                    pass


class _SafeStreamHandler(logging.StreamHandler):
    """Console handler that tolerates unencodable characters and broken/closed streams."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stream = self.stream
            if stream is None:
                return
            msg = self.format(record) + self.terminator
            try:
                stream.write(msg)
            except UnicodeEncodeError:
                enc = getattr(stream, "encoding", None) or "ascii"
                stream.write(msg.encode(enc, "replace").decode(enc, "replace"))
            self.flush()
        except RecursionError:
            raise
        except Exception:  # console is best effort; never propagate
            pass


def _role(handler: logging.Handler) -> str | None:
    return getattr(handler, _TAG, None)


def _find(root: logging.Logger, role: str) -> logging.Handler | None:
    for h in root.handlers:
        if _role(h) == role:
            return h
    return None


def _remove(root: logging.Logger, handler: logging.Handler) -> None:
    root.removeHandler(handler)
    try:
        handler.close()
    except Exception:
        pass


def _norm(path: Path | str) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(path)))
    except Exception:
        return str(path)


def _open_file_handler(wanted: Path) -> tuple[logging.Handler | None, Path]:
    """Rotating handler on ``wanted``, or on a temp-dir fallback; (None, wanted) if both fail."""
    candidates = [wanted]
    try:
        candidates.append(Path(tempfile.gettempdir()) / paths.APP_DIR_NAME / "logs" / LOG_FILE_NAME)
    except Exception:
        pass
    for cand in candidates:
        try:
            cand.parent.mkdir(parents=True, exist_ok=True)
            handler = _SafeRotatingFileHandler(
                str(cand), maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
            )
            return handler, cand
        except Exception as exc:
            _stderr_note(f"TreeAI Coach: cannot open log file {cand}: {exc}")
    return None, wanted


def _stderr_usable() -> bool:
    err = sys.stderr
    return err is not None and hasattr(err, "write") and not getattr(err, "closed", False)


def _stderr_note(text: str) -> None:
    """Last-resort message on stderr (when logging itself cannot be set up)."""
    try:
        if _stderr_usable():
            sys.stderr.write(text + "\n")
    except Exception:
        pass


def _header(path: Path, debug: bool) -> None:
    try:
        from treeaicoach import APP_NAME, __version__
    except Exception:  # pragma: no cover - package metadata missing
        APP_NAME, __version__ = "TreeAI Coach", "?"
    try:
        plat = platform.platform(terse=True)
    except Exception:
        plat = sys.platform
    log.info(
        "%s %s | Python %s | %s | frozen=%s | debug=%s | log=%s",
        APP_NAME, __version__, platform.python_version(), plat, paths.is_frozen(), debug, path,
    )


def setup_logging(debug: bool = False, console: bool | None = None) -> Path:
    """Configure the root logger; returns the log file path. Idempotent, never raises.

    ``debug``: DEBUG level instead of INFO. ``console``: None = automatic (only if
    ``sys.stderr`` exists), False = never, True = if ``sys.stderr`` exists.
    """
    global _log_file
    level = logging.DEBUG if debug else logging.INFO
    wanted = Path(LOG_FILE_NAME)
    try:
        wanted = paths.logs_dir() / LOG_FILE_NAME
        with _lock:
            root = logging.getLogger()
            root.setLevel(level)
            if paths.is_frozen():
                logging.raiseExceptions = False   # never print handler errors in the exe
            formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

            # --- file
            created = False
            fh = _find(root, "file")
            if fh is not None and getattr(fh, "_treeaicoach_wanted", None) != _norm(wanted):
                _remove(root, fh)
                fh = None
            if fh is None:
                fh, actual = _open_file_handler(wanted)
                if fh is not None:
                    setattr(fh, _TAG, "file")
                    setattr(fh, "_treeaicoach_wanted", _norm(wanted))
                    setattr(fh, "_treeaicoach_path", actual)
                    root.addHandler(fh)
                    created = True
                _log_file = actual if fh is not None else None
            if fh is not None:
                fh.setLevel(level)
                fh.setFormatter(formatter)

            # --- console
            ch = _find(root, "console")
            allow_console = (console is None or bool(console)) and _stderr_usable()
            if not allow_console:
                if ch is not None:
                    _remove(root, ch)
            else:
                if ch is None:
                    ch = _SafeStreamHandler(sys.stderr)
                    setattr(ch, _TAG, "console")
                    root.addHandler(ch)
                elif isinstance(ch, logging.StreamHandler) and ch.stream is not sys.stderr:
                    ch.setStream(sys.stderr)
                ch.setLevel(level)
                ch.setFormatter(formatter)

            for name, lvl in QUIET_LOGGERS.items():
                logging.getLogger(name).setLevel(max(lvl, level))
            logging.captureWarnings(True)

            result = _log_file if _log_file is not None else wanted
        if created:
            _header(result, debug)
        else:
            log.debug("Logging reconfigured (debug=%s, console=%s)", debug, console)
        return result
    except Exception as exc:  # defensive: logging setup must never crash the app
        _stderr_note(f"TreeAI Coach: logging setup failed: {exc!r}")
        return wanted


def current_log_file() -> Path | None:
    """Path of the active log file (None if setup_logging() did not open one)."""
    return _log_file


def close_logging() -> None:
    """Flush/close our handlers and the faulthandler file (app exit, tests). Never raises."""
    global _log_file, _fault_file
    try:
        with _lock:
            root = logging.getLogger()
            for h in list(root.handlers):
                if _role(h) is not None:
                    _remove(root, h)
            _log_file = None
            if _fault_file is not None:
                try:
                    faulthandler.disable()
                except Exception:
                    pass
                try:
                    _fault_file.close()
                except Exception:
                    pass
                _fault_file = None
    except Exception:
        pass


# --------------------------------------------------------------------------- exception hooks


def _exc_info(exc_type: Any, exc: Any, tb: Any) -> Any:
    """A tuple usable as ``exc_info`` (or None if the pieces are unusable)."""
    if isinstance(exc_type, type) and issubclass(exc_type, BaseException):
        return (exc_type, exc if isinstance(exc, BaseException) else None, tb)
    if isinstance(exc, BaseException):
        return (type(exc), exc, tb)
    return None


def _log_uncaught(message: str, exc_type: Any, exc: Any, tb: Any, level: int = logging.CRITICAL) -> None:
    try:
        info = _exc_info(exc_type, exc, tb)
        if info is not None:
            log.log(level, message, exc_info=info)
        else:
            log.log(level, "%s: %s", message, _safe_repr(exc))
    except Exception:
        try:
            log.log(level, "%s (traceback unavailable)", message)
        except Exception:
            pass


def _safe_repr(obj: Any, limit: int = 200) -> str:
    try:
        r = repr(obj)
    except Exception:
        r = f"<{type(obj).__name__}>"
    return r if len(r) <= limit else r[: limit - 3] + "..."


def _make_sys_hook(previous: Any) -> Any:
    def _sys_excepthook(exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if isinstance(exc_type, type) and issubclass(exc_type, KeyboardInterrupt):
                log.info("Interrupted by the user (KeyboardInterrupt)")
                if callable(previous):
                    previous(exc_type, exc, tb)
                return
            _log_uncaught("Uncaught exception", exc_type, exc, tb)
        except Exception:
            pass

    setattr(_sys_excepthook, _HOOK_TAG, True)
    return _sys_excepthook


def _thread_excepthook(args: Any) -> None:
    try:
        exc_type = getattr(args, "exc_type", None)
        if isinstance(exc_type, type) and issubclass(exc_type, SystemExit):
            return  # same as the default hook: SystemExit in a thread is silent
        thread = getattr(args, "thread", None)
        name = getattr(thread, "name", None) or "?"
        _log_uncaught(f"Uncaught exception in thread {name}", exc_type,
                      getattr(args, "exc_value", None), getattr(args, "exc_traceback", None))
    except Exception:
        pass


setattr(_thread_excepthook, _HOOK_TAG, True)


def _unraisable_hook(unraisable: Any) -> None:
    try:
        msg = getattr(unraisable, "err_msg", None) or "Exception ignored in"
        obj = _safe_repr(getattr(unraisable, "object", None))
        _log_uncaught(f"{msg}: {obj}", getattr(unraisable, "exc_type", None),
                      getattr(unraisable, "exc_value", None), getattr(unraisable, "exc_traceback", None),
                      level=logging.WARNING)
    except Exception:
        pass


setattr(_unraisable_hook, _HOOK_TAG, True)


def _enable_faulthandler() -> None:
    """Dump tracebacks of native crashes (segfault, access violation) to logs/faults.log."""
    global _fault_file
    if _fault_file is not None and not _fault_file.closed:
        return
    try:
        path = paths.logs_dir() / FAULT_FILE_NAME
        mode = "a"
        try:
            if path.stat().st_size > MAX_BYTES:
                mode = "w"
        except OSError:
            pass
        fh = open(path, mode, encoding="utf-8")  # kept open for the process lifetime
        try:
            faulthandler.enable(file=fh, all_threads=True)
        except Exception:
            fh.close()
            raise
        _fault_file = fh
    except Exception as exc:
        log.debug("faulthandler not enabled: %s", exc)


def install_excepthooks(faulthandler_log: bool = True) -> None:
    """Log uncaught exceptions (main thread, other threads, unraisable). Idempotent, never raises."""
    try:
        with _lock:
            if not getattr(sys.excepthook, _HOOK_TAG, False):
                sys.excepthook = _make_sys_hook(sys.excepthook)
            if not getattr(threading.excepthook, _HOOK_TAG, False):
                threading.excepthook = _thread_excepthook
            if not getattr(sys.unraisablehook, _HOOK_TAG, False):
                sys.unraisablehook = _unraisable_hook
            if faulthandler_log:
                _enable_faulthandler()
    except Exception as exc:
        try:
            log.warning("Cannot install exception hooks: %s", exc)
        except Exception:
            pass
