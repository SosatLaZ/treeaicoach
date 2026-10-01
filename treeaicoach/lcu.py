"""Optional, read-only access to the League Client (LCU) local API, used AFTER a game only.

The League Client (``LeagueClientUx.exe``) serves a local REST API on ``https://127.0.0.1:<port>``
protected by HTTP basic auth (``riot:<password>``). Riot allows third-party apps to read it.
TreeAI Coach uses it for one thing: once a game is over, fetch the match and its timeline
(true per-minute positions of the 10 players, kill positions, objectives, gold / XP) to
enrich the post-game report and to score its own alerts (``ground_truth.py``). Nothing here
is used for live decisions, nothing is ever written to the client (GET requests only).

Discovery (:func:`discover`), cheapest first, all optional:

1. the ``lockfile`` (``LeagueClient:<pid>:<port>:<password>:<protocol>``) in the League
   install folder: ``TREEAI_LCU_DIR`` (env), the folders of ``RiotClientInstalls.json``
   (``%PROGRAMDATA%\\Riot Games``), the uninstall registry keys, the default install paths;
2. the command line of the running ``LeagueClientUx.exe`` (``--app-port``,
   ``--remoting-auth-token``, ``--install-directory``) read with WMI (``wmic``, then
   PowerShell ``Get-CimInstance``) in a hidden process (``CREATE_NO_WINDOW``).

Everything is disabled off Windows (unless credentials are injected, e.g. by the tests) and
when the client is not running. No function raises: failures give ``None`` / ``False``.

HTTP: ``urllib`` only, no proxy (``ProxyHandler({})``), always to ``127.0.0.1``; the client's
certificate is self-signed so verification is disabled, and only for that loopback host.

The lockfile / command-line parsing and the endpoint choice are adapted from lcu-driver
(MIT, (c) 2019 André Sousa), Willump (MIT, (c) 2021 Eleanor Silver) and League Akari
(MIT, (c) 2026 Hanxven): see THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import base64
import http.client
import json
import logging
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from treeaicoach.fmtutil import finite as _num

log = logging.getLogger(__name__)

HOST = "127.0.0.1"                      # the only host this module ever talks to
USERNAME = "riot"
PROCESS_NAME = "LeagueClientUx.exe"
LOCKFILE_NAME = "lockfile"
DEFAULT_INSTALL_DIRS: tuple[str, ...] = (
    r"C:\Riot Games\League of Legends",
    r"D:\Riot Games\League of Legends",
    r"E:\Riot Games\League of Legends",
    r"C:\Program Files\Riot Games\League of Legends",
    r"C:\Program Files (x86)\Riot Games\League of Legends",
)
RIOT_CLIENT_INSTALLS = Path("Riot Games") / "RiotClientInstalls.json"     # under %PROGRAMDATA%
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Riot Game league_of_legends.live"
ENV_DIR = "TREEAI_LCU_DIR"

# endpoints (League Akari's catalogue)
SUMMONER_PATH = "/lol-summoner/v1/current-summoner"
MATCHES_PATH = "/lol-match-history/v1/products/lol/current-summoner/matches?begIndex=0&endIndex=1"
GAME_PATH = "/lol-match-history/v1/games/{game_id}"
TIMELINE_PATH = "/lol-match-history/v1/game-timelines/{game_id}"

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
REDISCOVER_S = 10.0                     # lockfile scan at most every 10 s while not found
PROCESS_QUERY_S = 60.0                  # WMI / PowerShell query at most every 60 s
PROCESS_TIMEOUT_S = 6.0
POSTGAME_TIMEOUT_S = 120.0              # match data appears a while after the end screen
POSTGAME_POLL_S = 8.0
MATCH_START_TOL_S = 15 * 60.0           # gameCreation vs record start
MATCH_DURATION_TOL_S = 150.0            # gameDuration vs recorded duration

STATUS_DISABLED = "disabled"
STATUS_CONNECTED = "connected"
STATUS_NOT_FOUND = "not_found"

_PORT_RE = re.compile(r"--app-port=\"?(\d{2,5})")
_TOKEN_RE = re.compile(r"--remoting-auth-token=\"?([\w\-]+)")
_PID_RE = re.compile(r"--app-pid=\"?(\d+)")
_INSTALL_RE = re.compile(r"--install-directory=(?:\"([^\"]+)\"|([^\s\"]+(?:\s(?!--)[^\s\"]+)*))")


# ---------------------------------------------------------------------------------- credentials
@dataclass(frozen=True)
class LcuCredentials:
    """Port + password of the running client."""

    port: int
    password: str
    protocol: str = "https"
    pid: int | None = None
    source: str = ""

    @property
    def base_url(self) -> str:
        return f"{self.protocol}://{HOST}:{self.port}"

    def auth_header(self) -> str:
        raw = f"{USERNAME}:{self.password}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def __repr__(self) -> str:   # never log the password
        return f"LcuCredentials(port={self.port}, protocol={self.protocol!r}, source={self.source!r})"


def _valid_port(x: Any) -> int | None:
    try:
        p = int(str(x).strip())
    except (TypeError, ValueError):
        return None
    return p if 1 <= p <= 65535 else None


def parse_lockfile(text: Any, source: str = "lockfile") -> LcuCredentials | None:
    """``"LeagueClient:<pid>:<port>:<password>:<protocol>"`` -> credentials (None if malformed)."""
    try:
        parts = str(text or "").strip().split(":")
        if len(parts) < 5:
            return None
        port = _valid_port(parts[2])
        password = parts[3].strip()
        protocol = parts[4].strip().lower() or "https"
        if port is None or not password or protocol not in ("http", "https"):
            return None
        try:
            pid = int(parts[1])
        except ValueError:
            pid = None
        return LcuCredentials(port, password, protocol, pid, source)
    except Exception:
        return None


def parse_command_line(cmd: Any) -> LcuCredentials | None:
    """``LeagueClientUx.exe`` command line -> credentials (``--app-port`` + ``--remoting-auth-token``)."""
    try:
        s = str(cmd or "")
        m_port, m_tok = _PORT_RE.search(s), _TOKEN_RE.search(s)
        if not m_port or not m_tok:
            return None
        port = _valid_port(m_port.group(1))
        if port is None:
            return None
        m_pid = _PID_RE.search(s)
        return LcuCredentials(port, m_tok.group(1), "https", int(m_pid.group(1)) if m_pid else None, "process")
    except Exception:
        return None


def install_dir_from_command_line(cmd: Any) -> Path | None:
    """``--install-directory=...`` of the client command line (quoted or not)."""
    try:
        m = _INSTALL_RE.search(str(cmd or ""))
        if not m:
            return None
        raw = (m.group(1) or m.group(2) or "").strip().strip('"')
        return Path(raw) if raw else None
    except Exception:
        return None


def read_lockfile(folder: Path | str) -> LcuCredentials | None:
    """Credentials from ``<folder>/lockfile`` (None if missing / unreadable)."""
    try:
        p = Path(folder) / LOCKFILE_NAME
        if not p.is_file():
            return None
        # the client keeps the file open: read it in shared mode with a plain open()
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            return parse_lockfile(fh.read(512), source=str(p))
    except Exception as exc:
        log.debug("Cannot read the LCU lockfile in %s: %s", folder, exc)
        return None


# ---------------------------------------------------------------------------------- discovery
def _riot_client_installs_dirs() -> list[Path]:
    out: list[Path] = []
    try:
        base = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        p = Path(base) / RIOT_CLIENT_INSTALLS
        if not p.is_file():
            return out
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict):
            return out
        assoc = data.get("associated_client")
        if isinstance(assoc, dict):
            for k in assoc:
                if isinstance(k, str) and "league" in k.lower():
                    out.append(Path(k))
        for key in ("rc_default", "rc_live"):        # Riot Client path: League is usually a sibling
            v = data.get(key)
            if isinstance(v, str) and v:
                rc = Path(v)
                for parent in list(rc.parents)[:3]:
                    if parent.name.lower() == "riot games":
                        out.append(parent / "League of Legends")
                        break
    except Exception as exc:
        log.debug("RiotClientInstalls.json unreadable: %s", exc)
    return out


def _registry_dirs() -> list[Path]:
    out: list[Path] = []
    if sys.platform != "win32":
        return out
    try:
        import winreg  # type: ignore[import-not-found]

        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, UNINSTALL_KEY) as k:
                    val, _ = winreg.QueryValueEx(k, "InstallLocation")
                    if isinstance(val, str) and val.strip():
                        out.append(Path(val.strip().strip('"')))
            except OSError:
                continue
    except Exception as exc:
        log.debug("Registry lookup failed: %s", exc)
    return out


def candidate_dirs() -> list[Path]:
    """League install folders to look for the lockfile in (deduplicated, in priority order)."""
    dirs: list[Path] = []
    env = os.environ.get(ENV_DIR)
    if env:
        dirs.append(Path(env))
    dirs += _riot_client_installs_dirs()
    dirs += _registry_dirs()
    dirs += [Path(d) for d in DEFAULT_INSTALL_DIRS]
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        key = str(d).rstrip("\\/").lower()
        if key and key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _run_hidden(args: list[str], timeout: float = PROCESS_TIMEOUT_S) -> str:
    """stdout of a hidden console command ('' on any error)."""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0
        res = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=flags,
                             stdin=subprocess.DEVNULL)
        return (res.stdout or b"").decode("utf-8", errors="replace")
    except Exception as exc:
        log.debug("Command %s failed: %s", args[0] if args else "?", exc)
        return ""


def process_command_lines(runner: Callable[[list[str]], str] | None = None) -> list[str]:
    """Command lines of the running ``LeagueClientUx.exe`` (Windows only; [] elsewhere)."""
    if runner is None:
        if sys.platform != "win32":
            return []
        runner = _run_hidden
    out: list[str] = []
    try:
        text = runner(["wmic", "process", "where", f"name='{PROCESS_NAME}'", "get", "commandline",
                       "/format:list"])
        out = [ln.split("=", 1)[1].strip() for ln in text.splitlines()
               if ln.lower().startswith("commandline=") and len(ln) > 12]
        if not out:   # wmic is gone from recent Windows 11 builds
            ps = ("Get-CimInstance Win32_Process -Filter \"Name='" + PROCESS_NAME + "'\" | "
                  "Select-Object -ExpandProperty CommandLine")
            text = runner(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-Command", ps])
            out = [ln.strip() for ln in text.splitlines() if "--" in ln]
    except Exception as exc:
        log.debug("Client process query failed: %s", exc)
    return out


def discover(dirs: Iterable[Path] | None = None, *, use_process: bool = True,
             runner: Callable[[list[str]], str] | None = None) -> LcuCredentials | None:
    """Find the running client's credentials (see module docstring). Never raises."""
    try:
        for d in (candidate_dirs() if dirs is None else dirs):
            c = read_lockfile(d)
            if c is not None:
                return c
        if not use_process:
            return None
        for cmd in process_command_lines(runner):
            install = install_dir_from_command_line(cmd)
            if install is not None:
                c = read_lockfile(install)
                if c is not None:
                    return c
            c = parse_command_line(cmd)
            if c is not None:
                return c
    except Exception:
        log.exception("LCU discovery failed")
    return None


# ---------------------------------------------------------------------------------- client
class LcuClient:
    """Tiny read-only LCU HTTP client (thread-safe, never raises).

    ``credentials`` (tests) skips the discovery; ``discover_fn`` replaces it; ``enabled``
    defaults to "running on Windows" (or True when credentials / discover_fn are given).
    """

    def __init__(self, credentials: LcuCredentials | None = None, *,
                 discover_fn: Callable[[bool], LcuCredentials | None] | None = None,
                 enabled: bool | None = None, timeout: float = 4.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if enabled is None:
            enabled = sys.platform == "win32" or credentials is not None or discover_fn is not None
        self.enabled = bool(enabled)
        self.timeout = max(0.2, float(timeout))
        self._fixed = credentials
        self._creds = credentials
        self._discover_fn = discover_fn
        self._clock = clock
        self._lock = threading.Lock()
        self._next_scan = -1e18
        self._next_process = -1e18
        self.last_error: str = ""
        self._status: str = STATUS_DISABLED if not self.enabled else STATUS_NOT_FOUND
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False          # self-signed Riot certificate, loopback host only
        ctx.verify_mode = ssl.CERT_NONE
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                   urllib.request.HTTPSHandler(context=ctx))

    # ------------------------------------------------------------------ credentials
    def credentials(self) -> LcuCredentials | None:
        """Cached credentials, re-discovered (rate-limited) when unknown."""
        if not self.enabled:
            return None
        with self._lock:
            if self._creds is not None:
                return self._creds
            now = self._clock()
            if now < self._next_scan:
                return None
            self._next_scan = now + REDISCOVER_S
            use_process = now >= self._next_process
            if use_process:
                self._next_process = now + PROCESS_QUERY_S
        try:
            if self._discover_fn is not None:
                creds = self._discover_fn(use_process)
            else:
                creds = discover(use_process=use_process)
        except Exception:
            log.exception("LCU discovery failed")
            creds = None
        with self._lock:
            if creds is not None and self._creds is None:
                log.info("League Client found (%s)", creds.source or "?")
            self._creds = creds if creds is not None else self._creds
            return self._creds

    def forget(self) -> None:
        """Drop the cached credentials (client restarted / closed); next call re-discovers."""
        with self._lock:
            self._creds = self._fixed if self._fixed is not None and self._discover_fn is None else None
            if self._creds is None:
                self._next_scan = -1e18

    def available(self) -> bool:
        """True when credentials are known (no request made)."""
        return self.credentials() is not None

    # ------------------------------------------------------------------ HTTP
    def get(self, path: str, timeout: float | None = None) -> Any:
        """GET ``path`` and decode the JSON; ``None`` on any error (404 included). Never raises."""
        creds = self.credentials()
        if creds is None:
            return None
        try:
            if not isinstance(path, str) or not path.startswith("/") or "//" in path or "@" in path:
                self.last_error = "bad path"
                return None
            url = creds.base_url + path
            if urllib.parse.urlsplit(url).hostname != HOST:      # never anything but the loopback
                self.last_error = "bad host"
                return None
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "TreeAICoach",
                                                       "Authorization": creds.auth_header()})
            chunks: list[bytes] = []
            total = 0
            with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        self.last_error = "response too large"
                        return None
                    chunks.append(chunk)
            data = json.loads(b"".join(chunks).decode("utf-8-sig", errors="replace"))
            self.last_error = ""
            self._status = STATUS_CONNECTED
            return data
        except urllib.error.HTTPError as exc:
            try:
                exc.close()
            except Exception:
                pass
            self.last_error = f"HTTP {exc.code}"
            if exc.code in (401, 403):
                self.forget()        # stale lockfile / new client session
            else:
                self._status = STATUS_CONNECTED     # the client answered
            return None
        except (urllib.error.URLError, OSError, http.client.HTTPException, ssl.SSLError, ValueError) as exc:
            self.last_error = f"{type(exc).__name__}"
            if not isinstance(exc, ValueError):
                self.forget()        # client closed: find it again later
                self._status = STATUS_NOT_FOUND
            return None
        except Exception:
            log.exception("LCU request failed")
            self.last_error = "unexpected"
            return None

    # ------------------------------------------------------------------ status / endpoints
    def status(self, probe: bool = True) -> str:
        """``"connected"`` / ``"not_found"`` / ``"disabled"`` (probes the client when ``probe``)."""
        if not self.enabled:
            return STATUS_DISABLED
        if not probe:
            return self._status if self._creds is not None else STATUS_NOT_FOUND
        if self.credentials() is None:
            self._status = STATUS_NOT_FOUND
            return self._status
        data = self.get(SUMMONER_PATH, timeout=2.0)
        answered = data is not None or (self.last_error.startswith("HTTP") and self._creds is not None)
        self._status = STATUS_CONNECTED if answered else STATUS_NOT_FOUND
        return self._status

    def status_text(self, probe: bool = True) -> str:
        """French status line for the UI: ``"Client LoL : connecté"`` or ``"... : non trouvé"``."""
        st = self.status(probe)
        return "Client LoL : " + ("connecté" if st == STATUS_CONNECTED else "non trouvé")

    def current_summoner(self) -> dict | None:
        d = self.get(SUMMONER_PATH)
        return d if isinstance(d, dict) else None

    def last_game(self) -> dict | None:
        """The current summoner's most recent game (match-history list entry), or None."""
        d = self.get(MATCHES_PATH)
        games = None
        if isinstance(d, dict):
            g = d.get("games")
            games = g.get("games") if isinstance(g, dict) else g
        if not isinstance(games, list):
            return None
        games = [x for x in games if isinstance(x, dict) and x.get("gameId") is not None]
        if not games:
            return None
        return max(games, key=lambda x: _num(x.get("gameCreation")) or 0.0)

    def game(self, game_id: Any) -> dict | None:
        gid = _game_id(game_id)
        d = self.get(GAME_PATH.format(game_id=gid)) if gid is not None else None
        return d if isinstance(d, dict) else None

    def timeline(self, game_id: Any) -> dict | None:
        gid = _game_id(game_id)
        d = self.get(TIMELINE_PATH.format(game_id=gid)) if gid is not None else None
        if isinstance(d, dict) and isinstance(d.get("frames"), list) and d["frames"]:
            return d
        return None


def _game_id(x: Any) -> int | None:
    f = _num(x)
    return int(f) if f is not None and f > 0 else None


# ---------------------------------------------------------------------------------- shared client
_default: LcuClient | None = None
_default_lock = threading.Lock()


def get_default_client() -> LcuClient:
    """Process-wide client (shared by the engine and the UI status)."""
    global _default
    with _default_lock:
        if _default is None:
            # never query the real client / spawn WMI processes from the test suite (Windows CI)
            _default = LcuClient(enabled=False) if "pytest" in sys.modules else LcuClient()
        return _default


# ---------------------------------------------------------------------------------- post-game
def _record_start_epoch(record: dict) -> float | None:
    try:
        import datetime as _dt

        s = (record.get("meta") or {}).get("start") or (record.get("summary") or {}).get("start")
        if not s:
            return None
        return _dt.datetime.fromisoformat(str(s)).timestamp()
    except Exception:
        return None


def _my_champion_id(game: dict) -> int | None:
    parts = game.get("participants")
    if isinstance(parts, list) and len(parts) == 1 and isinstance(parts[0], dict):
        f = _num(parts[0].get("championId"))
        return int(f) if f is not None else None
    return None


def game_matches_record(game: Any, record: Any, alias_of: Callable[[int], str | None] | None = None) -> bool:
    """Is this match-history entry the game of ``record``? (start time, duration, champion)."""
    try:
        if not isinstance(game, dict) or not isinstance(record, dict):
            return False
        created = _num(game.get("gameCreation"))
        start = _record_start_epoch(record)
        checks = 0
        if created is not None and start is not None:
            checks += 1
            if abs(created / 1000.0 - start) > MATCH_START_TOL_S:
                return False
        dur, rdur = _num(game.get("gameDuration")), _num(record.get("duration"))
        if dur is not None and rdur is not None and rdur > 120 and not record.get("incomplete"):
            if dur > 100000:            # some versions report milliseconds
                dur /= 1000.0
            checks += 1
            if abs(dur - rdur) > MATCH_DURATION_TOL_S:
                return False
        cid = _my_champion_id(game)
        champ = str((record.get("meta") or {}).get("champion") or "")
        if cid is not None and champ and alias_of is not None:
            alias = alias_of(cid)
            if alias:
                checks += 1
                if "".join(c for c in alias.lower() if c.isalnum()) != "".join(c for c in champ.lower() if c.isalnum()):
                    return False
        return checks > 0
    except Exception:
        log.exception("game_matches_record failed")
        return False


def default_alias_of() -> Callable[[int], str | None]:
    """championId -> alias using the bundled champion index (None when unknown)."""
    def alias_of(cid: int) -> str | None:
        try:
            from treeaicoach.champions import get_default_db

            e = get_default_db().by_key(int(cid))
            return e.alias if e is not None else None
        except Exception:
            return None
    return alias_of


def fetch_postgame_truth(record: dict, client: LcuClient | None = None, *,
                         timeout_s: float = POSTGAME_TIMEOUT_S, poll_s: float = POSTGAME_POLL_S,
                         cancel: threading.Event | None = None,
                         alias_of: Callable[[int], str | None] | None = None,
                         clock: Callable[[], float] = time.monotonic) -> dict | None:
    """Wait (up to ``timeout_s``) for the client's match + timeline of ``record`` and return the
    compact ground truth (``ground_truth.build_truth``), or None. Never raises."""
    try:
        client = client or get_default_client()
        if not client.enabled:
            return None
        alias_of = alias_of or default_alias_of()
        cancel = cancel or threading.Event()
        deadline = clock() + max(0.0, float(timeout_s))
        from treeaicoach.ground_truth import build_truth

        attempt = 0
        while True:
            attempt += 1
            last = client.last_game()
            if last is not None and game_matches_record(last, record, alias_of):
                gid = last.get("gameId")
                timeline = client.timeline(gid)
                if timeline is not None:
                    full = client.game(gid)
                    me_pid = None
                    parts = last.get("participants")
                    if isinstance(parts, list) and len(parts) == 1 and isinstance(parts[0], dict):
                        me_pid = parts[0].get("participantId")
                    truth = build_truth(full if full and len(full.get("participants") or []) > 1 else last,
                                        timeline, me_participant_id=me_pid, record=record, alias_of=alias_of)
                    if truth is not None:
                        log.info("LCU ground truth fetched (game %s, attempt %d)", gid, attempt)
                        return truth
            if clock() + poll_s > deadline or cancel.wait(max(0.05, float(poll_s))):
                log.info("LCU match data not available (%s)", client.last_error or "no matching game")
                return None
    except Exception:
        log.exception("fetch_postgame_truth failed")
        return None
