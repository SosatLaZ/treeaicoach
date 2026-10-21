"""TreeAICoach.exe bootstrapper (the small exe the user downloads; docs/PACKAGING notes in the spec).

Layout of release/TreeAICoach.exe::

    [PyInstaller one-file stub built from this file] [zip of the one-dir app] [trailer JSON] [u64 len] [MAGIC]

At launch the stub reads the trailer (last bytes of its own file). If
``%LOCALAPPDATA%\\TreeAICoach\\app\\<version>-<sha8>\\.ok`` exists the app was already unpacked: it is
started directly (no 250 MB extraction to %TEMP% at every launch, the antivirus scans the DLLs once).
Otherwise the payload is checked (SHA-256), unpacked into a temporary folder, renamed into place, and
older versions are removed. The real exe gets the command line, ``TREEAICOACH_LAUNCHER`` (this exe's
path: the updater replaces it, autostart points to it) and the stub waits for it and returns its exit
code (--selftest / --ui-smoke keep working from scripts).

Standard library only (os, sys, json, struct, hashlib, zipfile, shutil, subprocess, time): the stub
itself is a few MB and starts in well under a second.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import zipfile

MAGIC = b"TACBOOT1"                 # last 8 bytes of a bundled exe
TRAILER_TAIL = 16                   # u64 little-endian JSON length + MAGIC
APP_NAME = "TreeAICoach"
OK_NAME = ".ok"
KEEP_ENV_PREFIXES = ("_PYI", "_MEI")
STALE_TMP_S = 3600.0


class BootError(Exception):
    """Unrecoverable bootstrap problem (shown to the user in French)."""


# ------------------------------------------------------------------ trailer
def make_trailer(meta: dict) -> bytes:
    raw = json.dumps(meta, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return raw + struct.pack("<Q", len(raw)) + MAGIC


def read_trailer(path: str) -> dict | None:
    """Trailer metadata of a bundled exe, or None (not a bundle)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size < TRAILER_TAIL:
                return None
            f.seek(size - TRAILER_TAIL)
            tail = f.read(TRAILER_TAIL)
            if tail[8:] != MAGIC:
                return None
            n = struct.unpack("<Q", tail[:8])[0]
            if n <= 0 or n > 65536 or n > size - TRAILER_TAIL:
                return None
            f.seek(size - TRAILER_TAIL - n)
            meta = json.loads(f.read(n).decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    try:
        off, ln = int(meta["offset"]), int(meta["size"])
    except (KeyError, TypeError, ValueError):
        return None
    if off < 0 or ln <= 0 or off + ln > size - TRAILER_TAIL - n:
        return None
    if not isinstance(meta.get("sha256"), str) or len(meta["sha256"]) != 64:
        return None
    return meta


class _Slice:
    """Read-only, seekable window [offset, offset+size) of a file (for zipfile)."""

    def __init__(self, f, offset: int, size: int) -> None:
        self._f, self._off, self._size, self._pos = f, offset, size, 0

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, pos: int, whence: int = 0) -> int:
        base = {0: 0, 1: self._pos, 2: self._size}[whence]
        self._pos = max(0, min(self._size, base + pos))
        return self._pos

    def read(self, n: int = -1) -> bytes:
        left = self._size - self._pos
        if n is None or n < 0 or n > left:
            n = left
        if n <= 0:
            return b""
        self._f.seek(self._off + self._pos)
        data = self._f.read(n)
        self._pos += len(data)
        return data

    def close(self) -> None:
        pass


def payload_sha256(path: str, meta: dict) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        f.seek(int(meta["offset"]))
        left = int(meta["size"])
        while left > 0:
            chunk = f.read(min(left, 1 << 20))
            if not chunk:
                break
            h.update(chunk)
            left -= len(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ install
def apps_root() -> str:
    base = os.environ.get("TREEAICOACH_APPS_DIR", "").strip()   # tests / portable override
    if base:
        return base
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        local = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(local, APP_NAME, "app")


def version_dir_name(meta: dict) -> str:
    ver = "".join(c for c in str(meta.get("version", "0")) if c.isalnum() or c in ".-_")[:32] or "0"
    return f"{ver}-{meta['sha256'][:8]}"


def is_installed(dest: str, meta: dict) -> bool:
    try:
        with open(os.path.join(dest, OK_NAME), encoding="ascii") as f:
            if f.read().strip() != meta["sha256"]:
                return False
        return os.path.isfile(os.path.join(dest, meta.get("exe", APP_NAME + ".exe")))
    except (OSError, ValueError):
        return False


def _extract(exe_path: str, meta: dict, tmp: str) -> None:
    with open(exe_path, "rb") as f:
        with zipfile.ZipFile(_Slice(f, int(meta["offset"]), int(meta["size"]))) as z:
            root = os.path.realpath(tmp)
            for info in z.infolist():
                target = os.path.realpath(os.path.join(tmp, info.filename))
                if not (target == root or target.startswith(root + os.sep)):
                    raise BootError(f"chemin invalide dans l'archive : {info.filename}")
            z.extractall(tmp)


def install(exe_path: str, meta: dict, root: str | None = None) -> str:
    """Make sure the payload of ``exe_path`` is unpacked; returns the version folder."""
    root = root or apps_root()
    dest = os.path.join(root, version_dir_name(meta))
    if is_installed(dest, meta):
        return dest
    os.makedirs(root, exist_ok=True)
    if payload_sha256(exe_path, meta) != meta["sha256"]:
        raise BootError("le fichier TreeAICoach.exe est incomplet ou abîmé (téléchargement interrompu ?). "
                        "Télécharge-le à nouveau.")
    tmp = os.path.join(root, f".tmp-{os.getpid()}-{int(time.time())}")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        _extract(exe_path, meta, tmp)
        with open(os.path.join(tmp, OK_NAME), "w", encoding="ascii") as f:
            f.write(meta["sha256"])
        if os.path.isdir(dest):           # half-installed / damaged copy
            shutil.rmtree(dest, ignore_errors=True)
        try:
            os.rename(tmp, dest)
        except OSError:
            if not is_installed(dest, meta):     # another instance won the race: use its copy
                raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not is_installed(dest, meta):
        raise BootError(f"installation incomplète dans {dest}")
    return dest


def cleanup_old(root: str, keep: str) -> list[str]:
    """Remove other version folders (in-use files are skipped silently) and stale temp folders."""
    removed = []
    try:
        names = os.listdir(root)
    except OSError:
        return removed
    keep_name = os.path.basename(keep)
    now = time.time()
    for name in names:
        p = os.path.join(root, name)
        if name == keep_name or not os.path.isdir(p):
            continue
        if name.startswith(".tmp-"):
            try:
                if now - os.path.getmtime(p) < STALE_TMP_S:
                    continue               # maybe another instance unpacking right now
            except OSError:
                continue
        shutil.rmtree(p, ignore_errors=True)
        if not os.path.exists(p):
            removed.append(name)
    return removed


def child_env(exe_path: str, base: dict | None = None) -> dict:
    env = {k: v for k, v in (base if base is not None else os.environ).items()
           if not k.upper().startswith(KEEP_ENV_PREFIXES)}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"     # the app's bootloader is not our child
    env["TREEAICOACH_LAUNCHER"] = os.path.abspath(exe_path)
    env.setdefault("TREEAICOACH_LAUNCH_T0", repr(_T0_WALL))
    return env


# ------------------------------------------------------------------ main
_T0_WALL = time.time()


def _message(text: str) -> None:
    try:
        sys.stderr.write(text + "\n")
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, "TreeAI Coach", 0x10)
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    exe_path = os.path.abspath(sys.executable)
    meta = read_trailer(exe_path)
    if meta is None:
        _message("TreeAI Coach : ce fichier ne contient pas l'application (exe incomplet). "
                 "Télécharge à nouveau TreeAICoach.exe.")
        return 2
    try:
        root = apps_root()
        dest = install(exe_path, meta, root)
    except (BootError, OSError, zipfile.BadZipFile) as exc:
        _message("TreeAI Coach n'a pas pu se préparer au premier lancement :\n"
                 f"{exc}\n\nVérifie l'espace disque libre puis relance TreeAICoach.exe.")
        return 3
    try:
        cleanup_old(root, dest)
    except Exception:
        pass
    app = os.path.join(dest, meta.get("exe", APP_NAME + ".exe"))
    try:
        proc = subprocess.Popen([app] + argv, env=child_env(exe_path), close_fds=True)
    except OSError as exc:
        _message(f"TreeAI Coach n'a pas pu démarrer :\n{exc}\n\n(fichier : {app})")
        return 4
    try:
        return int(proc.wait())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
