"""TreeAICoach.exe: the small launcher / updater the user downloads (PyInstaller one-file, stdlib only).

The real app is a PyInstaller one-DIR build published as chunks next to ``release/version.json``::

    release/TreeAICoach.exe                  this launcher (version.json describes it: sha256 / size)
    release/app/manifest.json                {"version", "exe", "sha256", "chunks": [{"name", "size", "sha256"}]}
    release/app/<version>/part-NN.zip        independent zips (<= 90 MB each: GitHub file limit)

Launch: the installed app (``%LOCALAPPDATA%\\TreeAICoach\\app\\<version>``) starts at once, no network
(the app checks for updates itself). With nothing installed (first launch, or after the 2.13 in-app
updater swapped the old one-file exe for this launcher) the manifest is read, the chunks are
downloaded (progress window, resume, SHA-256) and unpacked once. The app then never re-extracts
anything at launch (the old one-file exe unpacked ~250 MB to %TEMP% every time, antivirus included).

One-button updates: the app's "Mettre à jour" downloads the new launcher (version.json, as before) and
runs it with ``--tac-install-self <launcher path> --tac-wait-pid <app pid>``: it waits for the app to
close, replaces the launcher (rename trick: a running exe can be renamed), installs the new app version,
writes the update status for the app, and starts it. The previous version is kept until the new one
has run 30 s (or exited cleanly); a new version that crashes at start rolls back to it.

The child gets the command line (``--selftest`` etc.), ``TREEAICOACH_LAUNCHER`` (this exe's path) and the
launcher waits for it and returns its exit code.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile

APP_NAME = "TreeAICoach"
APP_EXE = "TreeAICoach-app.exe"
GITHUB_OWNER, GITHUB_REPO, GITHUB_BRANCH = "SosatLaZ", "treeaicoach", "claude-team/brave-mendel-j8fqkf"
DEFAULT_CHANNEL_URL = (f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/"
                       f"{GITHUB_BRANCH}/release/version.json")
GITHUB_API = "https://api.github.com"
TOKEN_HOSTS = {"api.github.com", "raw.githubusercontent.com", "github.com"}
APP_MANIFEST_REL = "app/manifest.json"            # relative to version.json
OK_NAME = ".ok"
STATE_NAME = "state.json"
CONFIRM_S = 30.0
STALE_TMP_S = 3600.0
TIMEOUT_S = 30.0
MAX_MANIFEST = 256 * 1024
KEEP_ENV_PREFIXES = ("_PYI", "_MEI")
USER_AGENT = "TreeAICoach-launcher/1"
_T0_WALL = time.time()


class BootError(Exception):
    """Unrecoverable problem, message in French for the user."""


# ------------------------------------------------------------------ small helpers
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def version_key(v: str) -> tuple:
    out = []
    for part in str(v).split("-")[0].split("."):
        out.append(int(part) if part.isdigit() else -1)
    return tuple(out)


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path: str, data) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def apps_root() -> str:
    base = os.environ.get("TREEAICOACH_APPS_DIR", "").strip()      # tests / portable override
    if base:
        return base
    local = os.environ.get("LOCALAPPDATA", "").strip() or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(local, APP_NAME, "app")


def user_dir() -> str:
    env = os.environ.get("TREEAICOACH_HOME", "").strip()
    if env:
        return os.path.expandvars(os.path.expanduser(env))
    appdata = os.environ.get("APPDATA", "").strip() or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(appdata, APP_NAME)


# ------------------------------------------------------------------ manifest
def validate_manifest(m) -> dict:
    if not isinstance(m, dict):
        raise BootError("fichier de version de l'application invalide sur le serveur")
    ver, exe, chunks = m.get("version"), m.get("exe", APP_EXE), m.get("chunks")
    if not isinstance(ver, str) or not version_key(ver) or -1 in version_key(ver):
        raise BootError("numéro de version invalide sur le serveur")
    if not isinstance(exe, str) or "/" in exe or "\\" in exe or not exe.lower().endswith(".exe"):
        raise BootError("nom d'exécutable invalide sur le serveur")
    if not isinstance(chunks, list) or not chunks:
        raise BootError("liste des morceaux invalide sur le serveur")
    for c in chunks:
        name, sha, size = (c.get("name"), c.get("sha256"), c.get("size")) if isinstance(c, dict) else (None,) * 3
        if (not isinstance(name, str) or name.startswith(("/", "\\")) or ".." in name.split("/")
                or not isinstance(sha, str) or len(sha) != 64
                or isinstance(size, bool) or not isinstance(size, int) or size <= 0):
            raise BootError("morceau invalide dans le fichier de version du serveur")
    if not isinstance(m.get("sha256"), str) or len(m["sha256"]) != 64:
        raise BootError("empreinte de l'application invalide sur le serveur")
    return m


def manifest_sha(version: str, chunks: list) -> str:
    """Identity of a build: SHA-256 of its version and chunk hashes (in order)."""
    return hashlib.sha256((version + "\n" + "".join(c["sha256"] for c in chunks)).encode("ascii")).hexdigest()


# ------------------------------------------------------------------ network
class Channel:
    """Where version.json / app/manifest.json / chunks / launcher live (same rules as treeaicoach.updater)."""

    def __init__(self, url: str = "", token: str = "") -> None:
        self.token = (token or "").strip()
        self.base = (url or "").strip() or os.environ.get("TREEAICOACH_CHANNEL_URL", "").strip() or DEFAULT_CHANNEL_URL
        self.api = bool(self.token) and self.base == DEFAULT_CHANNEL_URL

    @classmethod
    def from_config(cls) -> "Channel":
        cfg = _read_json(os.path.join(user_dir(), "config.json"))
        cfg = cfg if isinstance(cfg, dict) else {}
        url, tok = cfg.get("update_channel_url", ""), cfg.get("github_token", "")
        return cls(url if isinstance(url, str) else "", tok if isinstance(tok, str) else "")

    def url(self, rel: str) -> str:
        """URL of ``rel`` (relative to the folder of version.json)."""
        if self.api:
            ref = urllib.parse.quote(GITHUB_BRANCH, safe="")
            path = urllib.parse.quote(f"release/{rel}")
            return f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}?ref={ref}"
        return urllib.parse.urljoin(self.base, urllib.parse.quote(rel))

    def request(self, url: str, start: int = 0) -> urllib.request.Request:
        h = {"User-Agent": USER_AGENT, "Cache-Control": "no-cache"}
        if self.api:
            h["Accept"] = "application/vnd.github.raw"
        if self.token and (urllib.parse.urlsplit(url).hostname or "").lower() in TOKEN_HOSTS:
            h["Authorization"] = f"Bearer {self.token}"
        if start > 0:
            h["Range"] = f"bytes={start}-"
        return urllib.request.Request(url, headers=h)

    def get_json(self, rel: str):
        with urllib.request.urlopen(self.request(self.url(rel)), timeout=TIMEOUT_S) as r:
            body = r.read(MAX_MANIFEST + 1)
        if len(body) > MAX_MANIFEST:
            raise BootError("fichier de version trop gros sur le serveur")
        return json.loads(body.decode("utf-8"))

    def download(self, rel: str, dest: str, size: int, sha: str, progress=None) -> None:
        """Download to ``dest`` (resumes ``dest.part``), check size + SHA-256."""
        if os.path.isfile(dest) and os.path.getsize(dest) == size and sha256_file(dest) == sha:
            if progress:
                progress(size)
            return
        part = dest + ".part"
        have = os.path.getsize(part) if os.path.isfile(part) else 0
        if have >= size:
            have = 0
        req = self.request(self.url(rel), have)
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            if have and getattr(r, "status", 200) != 206:      # server ignored Range: restart
                have = 0
            with open(part, "ab" if have else "wb") as f:
                done = have
                if progress:
                    progress(done)
                while True:
                    buf = r.read(256 * 1024)
                    if not buf:
                        break
                    f.write(buf)
                    done += len(buf)
                    if done > size:
                        break
                    if progress:
                        progress(done)
        if os.path.getsize(part) != size or sha256_file(part) != sha:
            os.remove(part)
            raise BootError(f"le morceau {os.path.basename(rel)} est arrivé abîmé (réessaie)")
        os.replace(part, dest)


# ------------------------------------------------------------------ install
def is_installed(dest: str) -> dict | None:
    """The manifest stored in ``dest/.ok`` when the folder holds a complete install."""
    ok = _read_json(os.path.join(dest, OK_NAME))
    if isinstance(ok, dict) and isinstance(ok.get("exe"), str) and os.path.isfile(os.path.join(dest, ok["exe"])):
        return ok
    return None


def dest_dir(root: str, m: dict) -> str:
    d = os.path.join(root, m["version"])
    ok = is_installed(d)
    if os.path.isdir(d) and not (ok and ok.get("sha256") == m["sha256"]) and ok is not None:
        d = os.path.join(root, f"{m['version']}-{m['sha256'][:8]}")       # republished build, same number
    return d


def install(ch: Channel, m: dict, root: str, progress=None) -> str:
    """Download (resume, verify) and unpack ``m``; returns the version folder."""
    m = validate_manifest(m)
    dest = dest_dir(root, m)
    ok = is_installed(dest)
    if ok and ok.get("sha256") == m["sha256"]:
        return dest
    dl = os.path.join(root, ".download")
    os.makedirs(dl, exist_ok=True)
    total = sum(c["size"] for c in m["chunks"])
    base = 0
    files = []
    for c in m["chunks"]:
        f = os.path.join(dl, c["sha256"][:16] + ".zip")

        def cb(done, _b=base):
            if progress:
                progress(_b + done, total)
        ch.download("app/" + c["name"], f, c["size"], c["sha256"], cb)
        files.append(f)
        base += c["size"]
    tmp = os.path.join(root, f".tmp-{os.getpid()}-{int(time.time())}")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        real = os.path.realpath(tmp)
        for f in files:
            with zipfile.ZipFile(f) as z:
                for n in z.namelist():
                    t = os.path.realpath(os.path.join(tmp, n))
                    if not t.startswith(real + os.sep):
                        raise BootError(f"chemin invalide dans l'archive : {n}")
                z.extractall(tmp)
        if not os.path.isfile(os.path.join(tmp, m["exe"])):
            raise BootError(f"{m['exe']} absent de l'archive")
        _write_json(os.path.join(tmp, OK_NAME), {k: m[k] for k in ("version", "exe", "sha256")})
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)
        try:
            os.rename(tmp, dest)
        except OSError:
            ok = is_installed(dest)
            if not (ok and ok.get("sha256") == m["sha256"]):    # not another instance's identical copy
                raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(dl, ignore_errors=True)
    return dest


def installed_versions(root: str) -> list[str]:
    try:
        names = [n for n in os.listdir(root) if not n.startswith(".") and is_installed(os.path.join(root, n))]
    except OSError:
        return []
    return sorted(names, key=version_key)


def load_state(root: str) -> dict:
    s = _read_json(os.path.join(root, STATE_NAME))
    return s if isinstance(s, dict) else {}


def save_state(root: str, state: dict) -> None:
    try:
        os.makedirs(root, exist_ok=True)
        _write_json(os.path.join(root, STATE_NAME), state)
    except OSError:
        pass


def current_dir(root: str) -> str | None:
    st = load_state(root)
    cur = st.get("current")
    if isinstance(cur, str) and cur and is_installed(os.path.join(root, cur)):
        return os.path.join(root, cur)
    names = installed_versions(root)
    return os.path.join(root, names[-1]) if names else None


def cleanup(root: str, keep: list[str]) -> list[str]:
    removed = []
    try:
        names = os.listdir(root)
    except OSError:
        return removed
    keep_names = {os.path.basename(k) for k in keep if k}
    now = time.time()
    for n in names:
        p = os.path.join(root, n)
        if n in keep_names or not os.path.isdir(p) or n == ".download":
            continue
        if n.startswith(".tmp-"):
            try:
                if now - os.path.getmtime(p) < STALE_TMP_S:
                    continue
            except OSError:
                continue
        shutil.rmtree(p, ignore_errors=True)
        if not os.path.exists(p):
            removed.append(n)
    return removed


# ------------------------------------------------------------------ progress window (Win32, ctypes)
class Progress:
    """Tiny native window "TreeAI Coach - installation" with a text line. No-op outside Windows."""

    def __init__(self, title: str = "TreeAI Coach") -> None:
        self.title, self.hwnd, self.label, self._last = title, None, None, 0.0
        self._ready = threading.Event()
        if os.name == "nt" and not os.environ.get("TREEAICOACH_NO_WINDOW"):
            threading.Thread(target=self._run, daemon=True).start()
            self._ready.wait(3.0)

    def _run(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes

            u = ctypes.windll.user32
            u.CreateWindowExW.restype = wintypes.HWND
            u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                          wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
            sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
            w, h = 460, 130
            ws_vis_cap = 0x10000000 | 0x00C00000 | 0x00080000          # VISIBLE | CAPTION | SYSMENU
            self.hwnd = u.CreateWindowExW(0x00000008, "STATIC", self.title, ws_vis_cap,
                                          (sw - w) // 2, (sh - h) // 2, w, h, None, None, None, None)
            self.label = u.CreateWindowExW(0, "STATIC", "Préparation…", 0x50000000 | 0x1,   # CHILD|VISIBLE|CENTER
                                           10, 30, w - 30, 40, self.hwnd, None, None, None)
            self._u = u
            self._ready.set()
            msg = wintypes.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            self._ready.set()

    def text(self, s: str) -> None:
        if self.label:
            try:
                self._u.SetWindowTextW(self.label, s)
            except Exception:
                pass

    def bytes(self, done: int, total: int, what: str) -> None:
        now = time.monotonic()
        if now - self._last < 0.2 and done < total:
            return
        self._last = now
        pct = int(100 * done / total) if total else 0
        self.text(f"{what}\n{done / 1e6:.0f} / {total / 1e6:.0f} Mo  ({pct} %)")

    def close(self) -> None:
        if self.hwnd:
            try:
                self._u.PostMessageW(self.hwnd, 0x0010, 0, 0)     # WM_CLOSE
            except Exception:
                pass
            self.hwnd = None


def message(text: str, error: bool = True) -> None:
    try:
        sys.stderr.write(text + "\n")
    except Exception:
        pass
    if os.name == "nt" and not os.environ.get("TREEAICOACH_NO_WINDOW"):
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, "TreeAI Coach", 0x10 if error else 0x40)
        except Exception:
            pass


# ------------------------------------------------------------------ processes
def child_env(launcher: str, base: dict | None = None) -> dict:
    env = {k: v for k, v in (base if base is not None else os.environ).items()
           if not k.upper().startswith(KEEP_ENV_PREFIXES)}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"          # the app's bootloader is not our child
    env["TREEAICOACH_LAUNCHER"] = os.path.abspath(launcher)
    env.setdefault("TREEAICOACH_LAUNCH_T0", repr(_T0_WALL))
    return env


def wait_pid(pid: int, timeout: float = 120.0) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        try:
            import ctypes

            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x00100000, False, int(pid))       # SYNCHRONIZE
            if h:
                k.WaitForSingleObject(h, int(timeout * 1000))
                k.CloseHandle(h)
        except Exception:
            pass
        return
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)


def replace_running_exe(src: str, target: str, attempts: int = 60) -> None:
    """Put ``src`` at ``target`` even if ``target`` is still running (rename it away first)."""
    if os.path.abspath(src).lower() == os.path.abspath(target).lower():
        return
    old = target + ".old"
    for i in range(attempts):
        try:
            if os.path.exists(old):
                try:
                    os.remove(old)
                except OSError:
                    old = f"{target}.{os.getpid()}-{i}.old"
            if os.path.exists(target):
                os.replace(target, old)
            shutil.copyfile(src, target + ".new")
            os.replace(target + ".new", target)
            try:
                os.remove(old)
            except OSError:
                pass
            return
        except OSError:
            time.sleep(0.5)
    raise BootError(f"impossible de remplacer {target}")


def cleanup_old_launchers(launcher: str) -> None:
    folder, base = os.path.split(os.path.abspath(launcher))
    try:
        for n in os.listdir(folder):
            if n.startswith(base + ".") and n.endswith(".old"):
                try:
                    os.remove(os.path.join(folder, n))
                except OSError:
                    pass
    except OSError:
        pass


def run_app(app_dir: str, argv: list[str], launcher: str, popen=None) -> tuple[int, float]:
    ok = is_installed(app_dir) or {}
    exe = os.path.join(app_dir, ok.get("exe", APP_EXE))
    t0 = time.monotonic()
    proc = (popen or subprocess.Popen)([exe] + list(argv), env=child_env(launcher), close_fds=True)
    root = os.path.dirname(app_dir)
    try:
        rc = proc.wait(timeout=CONFIRM_S)
    except subprocess.TimeoutExpired:
        confirm(root, app_dir)
        rc = proc.wait()
    return int(rc), time.monotonic() - t0


def confirm(root: str, app_dir: str) -> None:
    st = load_state(root)
    name = os.path.basename(app_dir)
    if st.get("current") == name and st.get("unconfirmed"):
        st["unconfirmed"] = False
        save_state(root, st)
        cleanup(root, [name, st.get("previous", "")])


# ------------------------------------------------------------------ flows
def update(ch: Channel, root: str, launcher: str | None, ui: Progress | None = None) -> str:
    """Install the published version if it differs from the current one; returns the folder to run."""
    m = validate_manifest(ch.get_json(APP_MANIFEST_REL))
    cur = current_dir(root)
    cur_ok = is_installed(cur) if cur else None
    if cur_ok and cur_ok.get("sha256") == m["sha256"]:
        return cur
    what = f"Téléchargement de TreeAI Coach {m['version']}…"
    if ui:
        ui.text(what)
    new = install(ch, m, root, (lambda d, t: ui.bytes(d, t, what)) if ui else None)
    st = load_state(root)
    prev = os.path.basename(cur) if cur and cur != new else st.get("previous", "")
    save_state(root, {"current": os.path.basename(new), "previous": prev, "unconfirmed": bool(prev)})
    if not prev:
        cleanup(root, [new])
    lau = m.get("launcher")
    if launcher and isinstance(lau, dict) and isinstance(lau.get("sha256"), str) and getattr(sys, "frozen", False):
        try:
            if sha256_file(launcher) != lau["sha256"]:
                tmp = os.path.join(root, ".launcher.exe")
                ch.download("TreeAICoach.exe", tmp, int(lau["size"]), lau["sha256"])
                replace_running_exe(tmp, launcher)
                os.remove(tmp)
        except Exception as exc:          # the app still works with this launcher
            sys.stderr.write(f"launcher self-update skipped: {exc}\n")
    return new


def _pop(argv: list[str], flag: str) -> str | None:
    if flag in argv:
        i = argv.index(flag)
        val = argv[i + 1] if i + 1 < len(argv) else ""
        del argv[i:i + 2]
        return val
    return None


def _write_status(path: str | None, text: str) -> None:
    if path:
        try:
            with open(path, "w", encoding="ascii") as f:
                f.write(text + "\n")
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    me = os.path.abspath(sys.executable)
    root = apps_root()
    install_self = _pop(argv, "--tac-install-self")
    wait = _pop(argv, "--tac-wait-pid")
    status = _pop(argv, "--tac-status")
    force_update = "--tac-update" in argv
    argv = [a for a in argv if a != "--tac-update"]
    launcher = me
    if install_self:                     # started by the app's "Mettre à jour" (new launcher in updates/)
        try:
            wait_pid(int(wait or 0))
        except ValueError:
            pass
        try:
            replace_running_exe(me, install_self)
            launcher = install_self
        except BootError as exc:
            _write_status(status, "failed locked")
            message(f"Mise à jour impossible : {exc}.\nFerme TreeAI Coach puis relance TreeAICoach.exe.")
            return 5
        force_update = True
    cleanup_old_launchers(launcher)
    cur = current_dir(root)
    ui = None
    if force_update or cur is None:
        ui = Progress("TreeAI Coach - installation" if cur is None else "TreeAI Coach - mise à jour")
        try:
            os.makedirs(root, exist_ok=True)
            cur = update(Channel.from_config(), root, launcher, ui)
            _write_status(status, "ok")
        except Exception as exc:  # noqa: BLE001  (network, disk, server)
            _write_status(status, "failed download")
            if cur is None:
                ui.close()
                message("TreeAI Coach n'a pas pu s'installer :\n"
                        f"{exc}\n\nUne connexion Internet est nécessaire pour le premier lancement. "
                        "Vérifie ta connexion et l'espace disque, puis relance TreeAICoach.exe.")
                return 3
            sys.stderr.write(f"update failed, running the installed version: {exc}\n")
        ui.close()
    if install_self:                     # hand over to the installed launcher (it owns the app's lifetime)
        try:
            subprocess.Popen([launcher] + argv, env={k: v for k, v in os.environ.items()
                                                     if not k.upper().startswith(KEEP_ENV_PREFIXES)}
                             | {"PYINSTALLER_RESET_ENVIRONMENT": "1"}, close_fds=True)
            return 0
        except OSError:
            pass
    while True:
        try:
            rc, ran = run_app(cur, argv, launcher)
        except OSError as exc:
            message(f"TreeAI Coach n'a pas pu démarrer :\n{exc}")
            return 4
        st = load_state(root)
        name = os.path.basename(cur)
        if st.get("current") == name and st.get("unconfirmed"):
            prev = st.get("previous", "")
            if rc != 0 and ran < CONFIRM_S and prev and is_installed(os.path.join(root, prev)):
                save_state(root, {"current": prev, "previous": "", "bad": name})
                message(f"La nouvelle version ({name}) n'a pas démarré correctement : retour à la version {prev}.")
                cur = os.path.join(root, prev)
                continue
            if rc == 0:
                confirm(root, cur)
        return rc


if __name__ == "__main__":
    sys.exit(main())
