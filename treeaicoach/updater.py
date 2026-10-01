"""In-app updates: check the published build, download it, verify it, swap the exe and relaunch.

The published build lives in the GitHub repository ``SosatLaZ/treeaicoach`` (branch
``claude-team/brave-mendel-j8fqkf``): ``release/TreeAICoach.exe`` described by
``release/version.json``::

    {"version": "1.4.0", "sha256": "<64 hex>", "size": 78877719,
     "notes": "…", "url": "<optional direct download URL>"}

Flow (every public function returns a result object and **never raises**; messages are French):

1. :func:`check_for_update` downloads the manifest (raw URL, or the GitHub contents API with
   ``Authorization: Bearer <token>`` when ``cfg.github_token`` is set: the repository is private)
   and compares versions semantically.
2. :func:`download_update` streams the exe to ``%APPDATA%\\TreeAICoach\\updates\\TreeAICoach-<ver>.exe``
   (``.part`` file + ``os.replace``), with a progress callback, then checks size and SHA-256.
3. :func:`apply_update` (frozen Windows exe only) writes a small ``.bat`` that waits for this
   process to exit, replaces ``sys.executable``, relaunches it and deletes itself; the batch is
   started detached and the caller closes the app. Paths reach the batch through environment
   variables, so accented user names are safe.

Networking uses plain ``urllib`` (system/env proxies respected) with timeouts. The token is only
sent to GitHub hosts and is dropped on a redirect to another host.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from treeaicoach import __version__, paths

log = logging.getLogger(__name__)

GITHUB_OWNER = "SosatLaZ"
GITHUB_REPO = "treeaicoach"
GITHUB_BRANCH = "claude-team/brave-mendel-j8fqkf"
MANIFEST_PATH = "release/version.json"
EXE_PATH = "release/TreeAICoach.exe"
EXE_NAME = "TreeAICoach.exe"
GITHUB_API = "https://api.github.com"
DEFAULT_CHANNEL_URL = (f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/"
                       f"{GITHUB_BRANCH}/{MANIFEST_PATH}")
#: Hosts that may receive the GitHub token (tests add their local server).
TOKEN_HOSTS: set[str] = {"api.github.com", "raw.githubusercontent.com", "github.com"}

USER_AGENT = f"TreeAICoach/{__version__} (updater)"
MANIFEST_TIMEOUT_S = 10.0
DOWNLOAD_TIMEOUT_S = 30.0         # per socket operation (not the whole download)
MAX_MANIFEST_BYTES = 64 * 1024
MAX_EXE_BYTES = 1024 * 1024 * 1024
MAX_NOTES_LEN = 2000
CHUNK = 256 * 1024
UPDATES_DIR_NAME = "updates"

# result statuses
UP_TO_DATE = "up_to_date"
AVAILABLE = "available"
ERROR = "error"

ProgressFn = Callable[[int, int], None]     # (bytes done, total bytes)


# --------------------------------------------------------------------------- versions

_VERSION_RE = re.compile(r"^\s*v?(\d{1,6}(?:\.\d{1,6}){0,3})(?:[-+.]?([0-9A-Za-z.\-]*))?\s*$")


def parse_version(text: Any) -> tuple[int, ...] | None:
    """``"1.4.0"`` -> ``(1, 4, 0, 1)``; a pre-release (``1.4.0-beta``) sorts before the release
    (last item 0). ``None`` if unparsable."""
    if not isinstance(text, str):
        return None
    m = _VERSION_RE.match(text)
    if m is None:
        return None
    nums = [int(p) for p in m.group(1).split(".")]
    while len(nums) < 4:
        nums.append(0)
    return (*nums, 0 if m.group(2) else 1)


def is_newer(candidate: Any, current: Any) -> bool:
    """True when ``candidate`` is strictly newer than ``current`` (unparsable -> False)."""
    a, b = parse_version(candidate), parse_version(current)
    if a is None:
        return False
    if b is None:
        return True
    return a > b


# --------------------------------------------------------------------------- results


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    sha256: str
    size: int
    notes: str = ""
    url: str = ""            # direct download URL ("" = next to the manifest)


@dataclass(frozen=True)
class CheckResult:
    status: str              # UP_TO_DATE | AVAILABLE | ERROR
    message: str
    current: str = __version__
    info: UpdateInfo | None = None
    can_install: bool = False   # True when available and running as a frozen Windows exe

    @property
    def available(self) -> bool:
        return self.status == AVAILABLE


@dataclass(frozen=True)
class DownloadResult:
    ok: bool
    message: str
    path: Path | None = None


@dataclass(frozen=True)
class ApplyResult:
    ok: bool
    message: str
    script: Path | None = None


# --------------------------------------------------------------------------- HTTP


class _UpdateError(Exception):
    """Internal: carries a French message for the user."""


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Drop the Authorization header when a redirect leaves the original host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            old_host = urllib.parse.urlsplit(req.full_url).hostname
            if urllib.parse.urlsplit(newurl).hostname != old_host:
                for k in list(new.headers):
                    if k.lower() == "authorization":
                        del new.headers[k]
                new.unredirected_hdrs.pop("Authorization", None)
        return new


def _opener() -> urllib.request.OpenerDirector:
    # build_opener() includes ProxyHandler() (env / Windows registry proxies).
    return urllib.request.build_opener(_SafeRedirect())


def _token_allowed(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in TOKEN_HOSTS


def _clean_token(token: Any) -> str:
    return token.strip() if isinstance(token, str) else ""


def _request(url: str, token: str, raw_api: bool) -> urllib.request.Request:
    headers = {"User-Agent": USER_AGENT, "Cache-Control": "no-cache"}
    if raw_api:
        headers["Accept"] = "application/vnd.github.raw"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    if token and _token_allowed(url):
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def _http_message(exc: BaseException, token: str) -> str:
    """French message for a network failure."""
    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
        if code == 401:
            return ("Jeton GitHub refusé (invalide ou expiré) : vérifie-le dans Réglages."
                    if token else "Accès refusé : dépôt privé, ajoute un jeton GitHub.")
        if code == 403:
            try:
                remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
            except Exception:
                remaining = None
            if remaining == "0":
                return "Trop de requêtes vers GitHub : réessaie dans quelques minutes."
            return ("Accès refusé : le jeton GitHub n'a pas le droit de lire le dépôt."
                    if token else "Accès refusé : dépôt privé, ajoute un jeton GitHub.")
        if code == 404:
            return ("Mise à jour introuvable : le jeton n'a pas accès au dépôt ou le fichier n'existe pas."
                    if token else "Accès refusé : dépôt privé, ajoute un jeton GitHub.")
        if code >= 500:
            return f"Le serveur de mises à jour est indisponible (erreur {code}). Réessaie plus tard."
        return f"Le serveur de mises à jour a répondu une erreur {code}."
    reason = getattr(exc, "reason", exc)
    if isinstance(exc, (socket.timeout, TimeoutError)) or isinstance(reason, (socket.timeout, TimeoutError)):
        return "Le serveur de mises à jour ne répond pas (délai dépassé). Réessaie plus tard."
    if isinstance(reason, ssl.SSLError) or isinstance(exc, ssl.SSLError):
        return "Connexion sécurisée impossible (certificat refusé) : vérifie ton réseau ou ton antivirus."
    if isinstance(exc, (urllib.error.URLError, OSError, ConnectionError)):
        return "Impossible de joindre le serveur de mises à jour : vérifie ta connexion Internet."
    return f"Erreur réseau inattendue : {exc}"


# --------------------------------------------------------------------------- channel


@dataclass(frozen=True)
class _Channel:
    manifest_url: str
    exe_url: str
    token: str
    api: bool                # GitHub contents API (Accept: raw)


def _api_contents_url(path: str) -> str:
    ref = urllib.parse.quote(GITHUB_BRANCH, safe="")
    return f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}?ref={ref}"


def _cfg_value(cfg: Any, name: str) -> Any:
    try:
        return getattr(cfg, name, None) if cfg is not None else None
    except Exception:
        return None


def resolve_channel(cfg: Any = None, *, url: str | None = None, token: str | None = None) -> _Channel:
    """Where to read the manifest / exe from (explicit args override ``cfg``)."""
    token = _clean_token(token if token is not None else _cfg_value(cfg, "github_token"))
    custom = url if url is not None else _cfg_value(cfg, "update_channel_url")
    custom = custom.strip() if isinstance(custom, str) else ""
    manifest = custom or DEFAULT_CHANNEL_URL
    if token and manifest == DEFAULT_CHANNEL_URL:
        return _Channel(_api_contents_url(MANIFEST_PATH), _api_contents_url(EXE_PATH), token, True)
    return _Channel(manifest, urllib.parse.urljoin(manifest, EXE_NAME), token, False)


def _is_http_url(url: Any) -> bool:
    if not isinstance(url, str) or len(url) > 4096:
        return False
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def parse_manifest(data: Any) -> UpdateInfo:
    """Validate a decoded ``version.json``; raises :class:`_UpdateError` (French) if unusable."""
    if not isinstance(data, Mapping):
        raise _UpdateError("Fichier de version invalide sur le serveur.")
    version = data.get("version")
    if parse_version(version) is None:
        raise _UpdateError("Fichier de version invalide sur le serveur (numéro de version).")
    sha = data.get("sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha.strip()):
        raise _UpdateError("Fichier de version invalide sur le serveur (empreinte SHA-256).")
    size = data.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or not (0 < size <= MAX_EXE_BYTES):
        raise _UpdateError("Fichier de version invalide sur le serveur (taille).")
    notes = data.get("notes", "")
    notes = notes.strip()[:MAX_NOTES_LEN] if isinstance(notes, str) else ""
    url = data.get("url", "")
    url = url.strip() if _is_http_url(url) else ""
    return UpdateInfo(str(version).strip(), sha.strip().lower(), size, notes, url)


def _fetch_manifest(ch: _Channel, timeout: float) -> UpdateInfo:
    req = _request(ch.manifest_url, ch.token, ch.api)
    try:
        with _opener().open(req, timeout=timeout) as resp:
            body = resp.read(MAX_MANIFEST_BYTES + 1)
    except Exception as exc:  # noqa: BLE001
        log.info("Update manifest fetch failed (%s): %r", ch.manifest_url, exc)
        raise _UpdateError(_http_message(exc, ch.token)) from exc
    if len(body) > MAX_MANIFEST_BYTES:
        raise _UpdateError("Fichier de version invalide sur le serveur (trop gros).")
    try:
        data = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _UpdateError("Fichier de version illisible sur le serveur.") from exc
    return parse_manifest(data)


def can_self_update() -> bool:
    """True when running as a frozen Windows exe (the only case where the exe can be swapped)."""
    return paths.is_frozen() and os.name == "nt"


# --------------------------------------------------------------------------- check


def check_for_update(cfg: Any = None, *, current: str | None = None, url: str | None = None,
                     token: str | None = None, timeout: float = MANIFEST_TIMEOUT_S) -> CheckResult:
    """Read the published manifest and compare with the running version. Never raises."""
    current = current or __version__
    try:
        ch = resolve_channel(cfg, url=url, token=token)
        info = _fetch_manifest(ch, timeout)
    except _UpdateError as exc:
        return CheckResult(ERROR, str(exc), current)
    except Exception as exc:  # noqa: BLE001 - never raise
        log.exception("Update check failed")
        return CheckResult(ERROR, f"Vérification impossible : {exc}", current)
    if not is_newer(info.version, current):
        return CheckResult(UP_TO_DATE, f"Déjà à jour (version {current}).", current, info)
    frozen = can_self_update()
    msg = f"Nouvelle version {info.version} disponible (tu as la {current})."
    if not frozen:
        msg += (" Lancé depuis les sources : mets à jour avec git pull."
                if not paths.is_frozen() else " Mise à jour automatique disponible sous Windows seulement.")
    return CheckResult(AVAILABLE, msg, current, info, can_install=frozen)


# --------------------------------------------------------------------------- download


def updates_dir() -> Path:
    """``user_data_dir()/updates`` (created if absent)."""
    d = paths.user_data_dir() / UPDATES_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_version(version: str) -> str:
    return re.sub(r"[^0-9A-Za-z.\-]", "_", version)[:40] or "new"


def file_sha256(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(CHUNK), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def verify_file(path: Path, sha256: str, size: int) -> bool:
    """Size and SHA-256 match."""
    try:
        if Path(path).stat().st_size != size:
            return False
    except OSError:
        return False
    return file_sha256(Path(path)) == sha256.lower()


def _call_progress(progress: ProgressFn | None, done: int, total: int) -> None:
    if progress is None:
        return
    try:
        progress(done, total)
    except Exception:
        log.debug("Update progress callback failed", exc_info=True)


def _cleanup_old(folder: Path, keep: Path) -> None:
    for p in folder.glob("TreeAICoach-*"):
        if p != keep:
            try:
                p.unlink()
            except OSError:
                pass


def download_update(info: UpdateInfo, cfg: Any = None, *, progress: ProgressFn | None = None,
                    dest_dir: Path | None = None, cancel: threading.Event | None = None,
                    url: str | None = None, token: str | None = None,
                    timeout: float = DOWNLOAD_TIMEOUT_S) -> DownloadResult:
    """Download and verify the new exe. Never raises."""
    try:
        folder = Path(dest_dir) if dest_dir is not None else updates_dir()
        folder.mkdir(parents=True, exist_ok=True)
        final = folder / f"TreeAICoach-{_safe_version(info.version)}.exe"
        if final.exists() and verify_file(final, info.sha256, info.size):
            _call_progress(progress, info.size, info.size)
            return DownloadResult(True, f"Version {info.version} déjà téléchargée et vérifiée.", final)
        ch = resolve_channel(cfg, url=url, token=token)
        if info.url:
            src, api = info.url, False
        else:
            src, api = ch.exe_url, ch.api
        part = final.with_name(final.name + ".part")
        # Fast path first: raw.githubusercontent.com (CDN, accepts the token for private repos);
        # the GitHub contents API is much slower for ~80 MB files and is only the fallback.
        sources: list[tuple[str, bool]] = []
        if not info.url and ch.api:
            sources.append((DEFAULT_CHANNEL_URL.rsplit("/", 1)[0] + "/" + EXE_NAME, False))
        sources.append((src, api))
        try:
            last_exc: Exception | None = None
            for s_url, s_api in sources:
                try:
                    _stream(s_url, ch.token, s_api, part, info, progress, cancel, timeout)
                    last_exc = None
                    break
                except _UpdateError as exc:
                    if cancel is not None and cancel.is_set():
                        raise
                    log.info("Update download via %s failed, trying next source", s_url)
                    last_exc = exc
            if last_exc is not None:
                raise last_exc
            if not verify_file(part, info.sha256, info.size):
                raise _UpdateError("Le fichier téléchargé est corrompu (empreinte SHA-256 différente). Réessaie.")
            _replace_retry(part, final)
        finally:
            try:
                part.unlink()
            except OSError:
                pass
        _cleanup_old(folder, final)
        return DownloadResult(True, f"Version {info.version} téléchargée et vérifiée.", final)
    except _UpdateError as exc:
        return DownloadResult(False, str(exc))
    except OSError as exc:
        log.exception("Update download: file error")
        return DownloadResult(False, f"Impossible d'enregistrer la mise à jour : {exc}")
    except Exception as exc:  # noqa: BLE001
        log.exception("Update download failed")
        return DownloadResult(False, f"Téléchargement impossible : {exc}")


def _stream(src: str, token: str, api: bool, part: Path, info: UpdateInfo,
            progress: ProgressFn | None, cancel: threading.Event | None, timeout: float) -> None:
    req = _request(src, token, api)
    done = 0
    try:
        with _opener().open(req, timeout=timeout) as resp, open(part, "wb") as out:
            _call_progress(progress, 0, info.size)
            while True:
                if cancel is not None and cancel.is_set():
                    raise _UpdateError("Téléchargement annulé.")
                block = resp.read(CHUNK)
                if not block:
                    break
                done += len(block)
                if done > info.size:
                    raise _UpdateError("Le fichier téléchargé est plus gros que prévu : mise à jour annulée.")
                out.write(block)
                _call_progress(progress, done, info.size)
            out.flush()
            os.fsync(out.fileno())
    except _UpdateError:
        raise
    except OSError as exc:
        if isinstance(exc, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, ssl.SSLError)):
            log.info("Update download failed (%s): %r", src, exc)
            raise _UpdateError(_http_message(exc, token)) from exc
        if isinstance(exc, urllib.error.HTTPError):
            raise _UpdateError(_http_message(exc, token)) from exc
        raise
    except Exception as exc:  # noqa: BLE001 (http.client errors etc.)
        log.info("Update download failed (%s): %r", src, exc)
        raise _UpdateError(_http_message(exc, token)) from exc
    if done != info.size:
        raise _UpdateError("Téléchargement incomplet (connexion interrompue). Réessaie.")


def _replace_retry(src: Path, dst: Path, attempts: int = 6, delay: float = 0.1) -> None:
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)


# --------------------------------------------------------------------------- apply

#: The batch only contains ASCII: paths come from environment variables (Unicode-safe).
UPDATE_SCRIPT = r"""@echo off
setlocal
set /a n=0
:wait
tasklist /FI "PID eq %TAC_PID%" /NH 2>nul | find " %TAC_PID% " >nul
if errorlevel 1 goto copy
set /a n+=1
if %n% GEQ 120 goto copy
ping -n 2 127.0.0.1 >nul
goto wait
:copy
set /a m=0
:retry
copy /b /y "%TAC_SRC%" "%TAC_DST%" >nul 2>&1
if not errorlevel 1 goto launch
set /a m+=1
if %m% GEQ 60 goto launch_old
ping -n 2 127.0.0.1 >nul
goto retry
:launch
del /f /q "%TAC_SRC%" >nul 2>&1
:launch_old
start "" "%TAC_DST%"
(goto) 2>nul & del /f /q "%~f0"
"""


def write_update_script(folder: Path) -> Path:
    """Write the swap batch atomically in ``folder``; returns its path."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"apply_update_{os.getpid()}.bat"
    fd, tmp = tempfile.mkstemp(prefix=".apply_", suffix=".tmp", dir=str(folder))
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\r\n") as f:
            f.write(UPDATE_SCRIPT)
        _replace_retry(Path(tmp), target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def script_env(new_exe: Path, target: Path, pid: int, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for the batch: paths + PID, PyInstaller variables removed so that the
    relaunched exe starts a fresh bootloader (not a "child" of the dying one)."""
    env = {k: v for k, v in (base if base is not None else os.environ).items()
           if not (k.upper().startswith("_PYI") or k.upper().startswith("_MEI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env["TAC_SRC"] = str(new_exe)
    env["TAC_DST"] = str(target)
    env["TAC_PID"] = str(int(pid))
    return env


def _dir_writable(folder: Path) -> bool:
    try:
        fd, tmp = tempfile.mkstemp(prefix=".tac_w", dir=str(folder))
        os.close(fd)
        os.unlink(tmp)
        return True
    except OSError:
        return False


def apply_update(new_exe: Path, info: UpdateInfo | None = None, *, target: Path | None = None,
                 pid: int | None = None, popen: Callable[..., Any] | None = None) -> ApplyResult:
    """Start the detached swap batch. On success the caller must close the app. Never raises."""
    try:
        new_exe = Path(new_exe)
        if target is None:
            if not paths.is_frozen():
                return ApplyResult(False, "Lancé depuis les sources : la mise à jour automatique ne concerne "
                                          "que la version .exe (utilise git pull).")
            target = Path(sys.executable)
        if os.name != "nt" and popen is None:
            return ApplyResult(False, "La mise à jour automatique ne fonctionne que sous Windows.")
        if info is not None and not verify_file(new_exe, info.sha256, info.size):
            return ApplyResult(False, "Le fichier de mise à jour est corrompu : télécharge-le à nouveau.")
        if not new_exe.is_file():
            return ApplyResult(False, "Fichier de mise à jour introuvable : télécharge-le à nouveau.")
        target = Path(target).resolve()
        if not _dir_writable(target.parent):
            return ApplyResult(False, f"Impossible de remplacer {target.name} : le dossier « {target.parent} » "
                                      "est protégé. Déplace TreeAI Coach dans un dossier personnel "
                                      "ou remplace le fichier à la main.")
        script = write_update_script(new_exe.parent)
        env = script_env(new_exe, target, pid if pid is not None else os.getpid())
        cmd = ["cmd.exe", "/d", "/c", str(script)]
        kwargs: dict[str, Any] = dict(env=env, cwd=str(new_exe.parent), close_fds=True,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
        runner = popen or subprocess.Popen
        # CREATE_NO_WINDOW gives cmd a hidden console that tasklist/ping inherit (with
        # DETACHED_PROCESS alone each console child would flash a window); new process group
        # + breakaway so that closing the app (or the PyInstaller job) does not kill it.
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
        last: Exception | None = None
        for flags in (no_window | group | breakaway, no_window | group):
            try:
                runner(cmd, creationflags=flags, **kwargs)
                log.info("Update script started: %s (target %s)", script, target)
                ver = f" {info.version}" if info is not None else ""
                return ApplyResult(True, f"Installation de la version{ver} : TreeAI Coach va redémarrer.", script)
            except OSError as exc:   # breakaway not allowed by the job -> retry without it
                last = exc
        try:
            script.unlink()
        except OSError:
            pass
        return ApplyResult(False, f"Impossible de lancer la mise à jour : {last}")
    except Exception as exc:  # noqa: BLE001
        log.exception("apply_update failed")
        return ApplyResult(False, f"Impossible de lancer la mise à jour : {exc}")


# --------------------------------------------------------------------------- publishing


def build_manifest(exe: Path, version: str, notes: str = "", url: str = "") -> dict[str, Any]:
    """Manifest dict describing ``exe`` (used to publish ``release/version.json``)."""
    exe = Path(exe)
    data: dict[str, Any] = {"version": version, "sha256": file_sha256(exe) or "",
                            "size": exe.stat().st_size, "notes": notes}
    if url:
        data["url"] = url
    return data


def write_manifest(exe: Path, out: Path, version: str | None = None, notes: str = "", url: str = "") -> Path:
    """Write ``version.json`` atomically next to (or wherever) ``out`` says."""
    data = build_manifest(exe, version or __version__, notes, url)
    out = Path(out)
    fd, tmp = tempfile.mkstemp(prefix=".version_", suffix=".tmp", dir=str(out.parent))
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, out)
    return out


def main(argv: list[str] | None = None) -> int:
    """``python -m treeaicoach.updater manifest [exe] [out] [notes]``: (re)write release/version.json."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "manifest":
        print("usage: python -m treeaicoach.updater manifest [exe] [out] [notes]")
        return 2
    root = Path(__file__).resolve().parent.parent
    exe = Path(args[1]) if len(args) > 1 else root / EXE_PATH
    out = Path(args[2]) if len(args) > 2 else root / MANIFEST_PATH
    notes = args[3] if len(args) > 3 else ""
    p = write_manifest(exe, out, notes=notes)
    print(p.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
