"""Filesystem locations: bundled resources and per-user data directories.

* Resources (textures, icons, ONNX model...) live in ``package_dir()/assets``. This works
  from a source checkout and inside a PyInstaller bundle (one-file: ``sys._MEIPASS``,
  one-dir: ``<exe dir>/_internal``), where data is bundled at ``<root>/treeaicoach/assets``.
* User data (config, logs, caches) lives in ``%APPDATA%\\TreeAICoach`` on Windows and
  ``~/.treeaicoach`` elsewhere. The environment variable ``TREEAICOACH_HOME`` overrides it
  (used by tests / CI).

User directories are created lazily on first use. None of the user-directory functions
ever raises: if a location is not writable, a folder in the system temp directory is used
instead (and a warning is logged once).
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

log = logging.getLogger(__name__)

PACKAGE_NAME = "treeaicoach"
APP_DIR_NAME = "TreeAICoach"          # Windows / temp fallback folder name
UNIX_DIR_NAME = ".treeaicoach"        # non-Windows folder name in the home directory
ENV_HOME = "TREEAICOACH_HOME"         # override of user_data_dir()
CONFIG_FILE_NAME = "config.json"

_lock = threading.Lock()
# candidate directory (str) -> directory actually used (the candidate itself or a fallback)
_resolved: dict[str, Path] = {}
_warned: set[str] = set()


def is_frozen() -> bool:
    """True when running from a PyInstaller (or similar) frozen executable."""
    return bool(getattr(sys, "frozen", False))


def _source_package_dir() -> Path:
    """Directory containing this file (the ``treeaicoach`` package in a checkout)."""
    here = globals().get("__file__") or ""
    try:
        return Path(here).resolve().parent
    except (OSError, RuntimeError, ValueError):
        return Path(os.path.abspath(here or ".")).parent


def package_dir() -> Path:
    """Directory of the ``treeaicoach`` package, also inside a PyInstaller bundle."""
    if is_frozen():
        candidates: list[Path] = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(str(meipass)) / PACKAGE_NAME)
        exe = getattr(sys, "executable", "") or ""
        if exe:
            exe_dir = Path(exe).parent
            candidates.append(exe_dir / "_internal" / PACKAGE_NAME)
            candidates.append(exe_dir / PACKAGE_NAME)
        for cand in candidates:
            try:
                if (cand / "assets").is_dir():
                    return cand
            except OSError:
                continue
        if candidates:
            return candidates[0]
    return _source_package_dir()


def asset_path(*parts: str | os.PathLike[str]) -> Path:
    """Path of a bundled resource: ``package_dir()/"assets"/parts...`` (may not exist)."""
    return package_dir().joinpath("assets", *(os.fspath(p) for p in parts))


def _home() -> Path:
    """User home directory, or the temp directory if it cannot be determined."""
    try:
        return Path.home()
    except Exception:  # RuntimeError / KeyError when HOME/USERPROFILE is unset
        return Path(tempfile.gettempdir())


def _candidate_user_dir() -> Path:
    """Preferred user data directory (not created, not checked)."""
    env = os.environ.get(ENV_HOME, "").strip()
    if env:
        p = Path(os.path.expandvars(os.path.expanduser(env)))
        if not p.is_absolute():
            try:
                p = Path(os.path.abspath(p))
            except OSError:
                pass
        return p
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            return Path(appdata) / APP_DIR_NAME
        return _home() / "AppData" / "Roaming" / APP_DIR_NAME
    return _home() / UNIX_DIR_NAME


def _make_writable_dir(path: Path) -> bool:
    """Create ``path`` if needed and check that a file can be written in it."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".probe-", suffix=".tmp", dir=str(path))
    except Exception as exc:  # OSError, ValueError (NUL in path)...
        log.debug("Directory %s not usable: %s", path, exc)
        return False
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.unlink(probe)
    except OSError:  # e.g. antivirus holding the file on Windows: still writable
        pass
    return True


def _temp_fallback(*sub: str) -> Path:
    try:
        base = Path(tempfile.gettempdir())
    except Exception:
        base = Path(".")
    return base.joinpath(APP_DIR_NAME, *sub)


def _fallback_dirs(*sub: str) -> Iterator[Path]:
    """Fallback locations, most stable first (the unique temp dir is only created if needed)."""
    yield _temp_fallback(*sub)
    try:
        yield Path(tempfile.mkdtemp(prefix=APP_DIR_NAME + "-")).joinpath(*sub)
    except Exception:
        return


def _usable_dir(candidate: Path, *fallback_sub: str) -> Path:
    """Return ``candidate`` if it can be created and written, else a temp-dir fallback.

    Results are cached per candidate (re-checked only if the directory disappeared).
    Never raises: in the worst case the (unusable) candidate itself is returned.
    """
    key = str(candidate)
    with _lock:
        cached = _resolved.get(key)
        if cached is not None:
            try:
                if cached.is_dir():
                    return cached
            except OSError:
                pass
        if _make_writable_dir(candidate):
            _resolved[key] = candidate
            return candidate
        for fb in _fallback_dirs(*fallback_sub):
            if _make_writable_dir(fb):
                if key not in _warned:
                    _warned.add(key)
                    log.warning("Directory %s is not writable, using %s instead", candidate, fb)
                _resolved[key] = fb
                return fb
        if key not in _warned:
            _warned.add(key)
            log.error("No writable directory found for %s", candidate)
        return candidate


def user_data_dir() -> Path:
    """Per-user data directory (``%APPDATA%\\TreeAICoach`` or ``~/.treeaicoach``); created if absent."""
    try:
        return _usable_dir(_candidate_user_dir())
    except Exception:  # defensive: never raise
        log.exception("user_data_dir() failed")
        return _temp_fallback()


def _user_subdir(name: str) -> Path:
    try:
        return _usable_dir(user_data_dir() / name, name)
    except Exception:  # defensive: never raise
        log.exception("Cannot resolve user sub-directory %s", name)
        return _temp_fallback(name)


def logs_dir() -> Path:
    """``user_data_dir()/logs`` (created if absent)."""
    return _user_subdir("logs")


def cache_dir() -> Path:
    """``user_data_dir()/cache`` (downloaded skin icons; created if absent)."""
    return _user_subdir("cache")


def collect_dir() -> Path:
    """``user_data_dir()/collect`` (minimap captures for re-training; created if absent)."""
    return _user_subdir("collect")


def config_path() -> Path:
    """``user_data_dir()/config.json`` (the file itself may not exist yet)."""
    return user_data_dir() / CONFIG_FILE_NAME


def _reset_cache() -> None:
    """Forget resolved directories (tests)."""
    with _lock:
        _resolved.clear()
        _warned.clear()
