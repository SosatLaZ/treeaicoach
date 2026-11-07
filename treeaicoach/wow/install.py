"""Find the World of Warcraft: Forever install and install / update the TreeAI addon in it.

Where WoW lives (first hit wins, the user's folder from Réglages first):

1. ``cfg.wow_path`` (the root ``World of Warcraft`` folder, a flavor folder ``_forever_``, or even its
   ``Interface/AddOns``),
2. the Windows registry (Blizzard ``InstallPath`` / the uninstall entry),
3. the Battle.net agent database / config (paths that contain "World of Warcraft"),
4. common folders on every drive (``Program Files (x86)``, ``Games``, ``Battle.net``...).

Inside the root, a "flavor" is a ``_name_`` folder (``_forever_`` at launch, ``_classic_beta_`` during the
beta, ``_classic_era_``, ``_retail_``...); Forever is preferred. The addon is copied into
``<flavor>/Interface/AddOns/TreeAI`` (atomic: copied next to it, then swapped). The player's settings
are not there (they live in ``WTF/.../SavedVariables``), so replacing the folder loses nothing.

Pure file operations, no process access. Never raises in the app path (results carry a French message).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import string
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

ADDON_NAME = "TreeAI"
#: Flavor folders by preference (Forever first). Any other ``_x_`` folder is accepted after these.
FLAVOR_ORDER: tuple[str, ...] = ("_forever_", "_forever_beta_", "_forever_ptr_", "_classic_beta_",
                                 "_classic_era_", "_classic_", "_anniversary_", "_retail_", "_ptr_", "_beta_")
_FLAVOR_RE = re.compile(r"^_[a-z0-9_]+_$", re.IGNORECASE)
WOW_EXES: tuple[str, ...] = ("WowForever.exe", "WowClassic.exe", "Wow.exe", "WowClassicB.exe", "WowClassicT.exe",
                             "WowB.exe", "WowT.exe")
COMMON_SUBDIRS: tuple[str, ...] = (r"Program Files (x86)\World of Warcraft", r"Program Files\World of Warcraft",
                                   r"World of Warcraft", r"Games\World of Warcraft", r"Jeux\World of Warcraft",
                                   r"Battle.net\World of Warcraft", r"Blizzard\World of Warcraft",
                                   r"Program Files (x86)\Battle.net\World of Warcraft",
                                   r"Blizzard Games\World of Warcraft")
REGISTRY_KEYS: tuple[tuple[str, str], ...] = (
    (r"SOFTWARE\WOW6432Node\Blizzard Entertainment\World of Warcraft", "InstallPath"),
    (r"SOFTWARE\Blizzard Entertainment\World of Warcraft", "InstallPath"),
    (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\World of Warcraft", "InstallLocation"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\World of Warcraft", "InstallLocation"),
)
COMBATLOG_TIP = ("Dans WoW, tape /combatlog (à refaire à chaque connexion) : le jeu écrit alors "
                 "Logs/WoWCombatLog.txt, que TreeAI lit comme Warcraft Logs. Rien d'autre n'est lu.")
CHATLOG_TIP = "Optionnel : /chatlog écrit Logs/WoWChatLog.txt (messages du groupe pour le coach)."
MAX_SCAN_BYTES = 4_000_000


@dataclass(frozen=True)
class WowInstall:
    root: Path                       # ...\World of Warcraft
    flavor_dir: Path                 # ...\World of Warcraft\_forever_
    flavor: str                      # "_forever_"
    source: str = ""                 # "réglages" / "registre" / "battle.net" / "dossier courant"

    @property
    def addons_dir(self) -> Path:
        return self.flavor_dir / "Interface" / "AddOns"

    @property
    def addon_dir(self) -> Path:
        return self.addons_dir / ADDON_NAME

    @property
    def logs_dir(self) -> Path:
        return self.flavor_dir / "Logs"

    @property
    def combat_log(self) -> Path:
        return self.logs_dir / "WoWCombatLog.txt"

    @property
    def chat_log(self) -> Path:
        return self.logs_dir / "WoWChatLog.txt"

    @property
    def is_forever(self) -> bool:
        return "forever" in self.flavor.lower()


@dataclass
class InstallResult:
    ok: bool
    status: str                      # "installed" / "updated" / "up_to_date" / "no_source" / "no_wow" / "error"
    message: str                     # French, for the UI
    version: str = ""
    path: Path | None = None
    details: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ the addon shipped with TreeAI
def addon_source_dir() -> Path | None:
    """``wow_addon/TreeAI`` shipped with the app: a patch drop in the user data folder first, then next
    to the package (frozen bundle or source checkout). None when absent."""
    cands: list[Path] = []
    try:
        from treeaicoach import paths  # noqa: PLC0415

        try:
            cands.append(paths.user_data_dir() / "wow_addon" / ADDON_NAME)
        except Exception:
            pass
        cands.append(paths.package_dir().parent / "wow_addon" / ADDON_NAME)
        cands.append(paths.package_dir() / "wow_addon" / ADDON_NAME)
    except Exception:
        log.debug("paths unavailable", exc_info=True)
    cands.append(Path(__file__).resolve().parents[2] / "wow_addon" / ADDON_NAME)
    for c in cands:
        try:
            if (c / f"{ADDON_NAME}.toc").is_file():
                return c
        except OSError:
            continue
    return None


def toc_fields(addon_dir: Path | None) -> dict[str, str]:
    """``## Key: value`` lines of ``<addon_dir>/TreeAI.toc`` (lower-case keys). {} when unreadable."""
    out: dict[str, str] = {}
    if addon_dir is None:
        return out
    try:
        text = (Path(addon_dir) / f"{ADDON_NAME}.toc").read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return out
    for line in text.splitlines()[:200]:
        m = re.match(r"^\s*##\s*([^:]+?)\s*:\s*(.*?)\s*$", line)
        if m:
            out.setdefault(m.group(1).strip().lower(), m.group(2))
    return out


def addon_version(addon_dir: Path | None) -> str:
    return toc_fields(addon_dir).get("version", "")


def _tree_signature(d: Path) -> dict[str, tuple[int, str]]:
    """{relative path: (size, sha1)} of the addon files (to know whether an update is needed)."""
    import hashlib  # noqa: PLC0415

    sig: dict[str, tuple[int, str]] = {}
    for p in sorted(d.rglob("*")):
        if p.is_file():
            data = p.read_bytes()
            sig[p.relative_to(d).as_posix()] = (len(data), hashlib.sha1(data).hexdigest())   # noqa: S324
    return sig


# ------------------------------------------------------------------ finding WoW
def _flavor_rank(name: str) -> int:
    low = name.lower()
    return FLAVOR_ORDER.index(low) if low in FLAVOR_ORDER else len(FLAVOR_ORDER) + (0 if "forever" in low else 1)


def flavors(root: Path) -> list[Path]:
    """The ``_x_`` client folders of a WoW root that look real (exe, Interface or WTF inside), best first."""
    out: list[Path] = []
    try:
        for d in Path(root).iterdir():
            if d.is_dir() and _FLAVOR_RE.match(d.name):
                if any((d / x).exists() for x in ("Interface", "WTF", "Logs", *WOW_EXES)):
                    out.append(d)
    except OSError:
        return []
    return sorted(out, key=lambda d: (_flavor_rank(d.name), d.name.lower()))


def _resolve(path: Path, source: str) -> WowInstall | None:
    """Accept a root, a flavor folder, ``Interface``, ``Interface/AddOns`` or ``AddOns/TreeAI``."""
    try:
        p = Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    for _ in range(4):                                  # climb out of Interface / AddOns / TreeAI
        if _FLAVOR_RE.match(p.name) and p.is_dir():
            return WowInstall(p.parent, p, p.name, source)
        if p.name.lower() in ("interface", "addons", ADDON_NAME.lower(), "wtf", "logs"):
            p = p.parent
            continue
        break
    if not p.is_dir():
        return None
    fl = flavors(p)
    if fl:
        return WowInstall(p, fl[0], fl[0].name, source)
    return None


def _registry_paths() -> list[str]:
    if sys.platform != "win32":
        return []
    out: list[str] = []
    try:
        import winreg  # noqa: PLC0415
    except ImportError:
        return out
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key, value in REGISTRY_KEYS:
            try:
                with winreg.OpenKey(hive, key) as k:
                    v, _t = winreg.QueryValueEx(k, value)
                    if isinstance(v, str) and v.strip():
                        out.append(v.strip())
            except OSError:
                continue
    return out


_PATH_IN_BLOB = re.compile(rb"([A-Za-z]:[\\/][^\x00-\x1f\"*?<>|]{0,200}?World of Warcraft)", re.IGNORECASE)


def paths_in_blob(data: bytes) -> list[str]:
    """Paths ending in "World of Warcraft" inside a binary / JSON blob (Battle.net product.db / config)."""
    seen: list[str] = []
    for m in _PATH_IN_BLOB.finditer(data[:MAX_SCAN_BYTES]):
        s = m.group(1).decode("utf-8", errors="ignore").replace("\\\\", "\\")
        if s not in seen:
            seen.append(s)
    return seen


def _battlenet_paths() -> list[str]:
    files = []
    for env, rel in (("PROGRAMDATA", r"Battle.net\Agent\product.db"), ("APPDATA", r"Battle.net\Battle.net.config"),
                     ("PROGRAMDATA", r"Battle.net\Agent\.patch.result")):
        base = os.environ.get(env)
        if base:
            files.append(Path(base) / rel)
    out: list[str] = []
    for f in files:
        try:
            if f.is_file():
                out += paths_in_blob(f.read_bytes())
        except OSError:
            continue
    return out


def _drives() -> list[str]:
    if sys.platform != "win32":
        return []
    return [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]


def candidate_roots(user_path: str = "") -> list[tuple[Path, str]]:
    """Places to look, in order, with where each came from."""
    out: list[tuple[Path, str]] = []
    if str(user_path or "").strip():
        out.append((Path(str(user_path).strip()), "réglages"))
    out += [(Path(p), "registre") for p in _registry_paths()]
    out += [(Path(p), "battle.net") for p in _battlenet_paths()]
    for drive in _drives():
        out += [(Path(drive) / sub, "dossier courant") for sub in COMMON_SUBDIRS]
    return out


def find_wow(user_path: str = "", roots: Iterable[tuple[Path, str]] | None = None) -> WowInstall | None:
    """The best WoW install (Forever preferred) or None. Never raises."""
    found: list[WowInstall] = []
    try:
        for path, source in (candidate_roots(user_path) if roots is None else roots):
            inst = _resolve(path, source)
            if inst is None:
                continue
            if source == "réglages":
                return inst                          # the user's choice wins, whatever the flavor
            found.append(inst)
            if inst.is_forever:
                return inst
    except Exception:
        log.debug("find_wow failed", exc_info=True)
    if not found:
        return None
    return sorted(found, key=lambda i: _flavor_rank(i.flavor))[0]


# ------------------------------------------------------------------ install / update
def installed_version(inst: WowInstall | None) -> str:
    return addon_version(inst.addon_dir) if inst is not None else ""


def needs_update(inst: WowInstall | None, src: Path | None = None) -> bool:
    """True when the installed addon is missing or differs from the shipped one."""
    src = src or addon_source_dir()
    if inst is None or src is None:
        return False
    try:
        if not inst.addon_dir.is_dir():
            return True
        return _tree_signature(src) != _tree_signature(inst.addon_dir)
    except OSError:
        return True


def install_addon(inst: WowInstall | None, src: Path | None = None, force: bool = False) -> InstallResult:
    """Copy the shipped addon into ``Interface/AddOns/TreeAI`` (atomic swap). Never raises."""
    if inst is None:
        return InstallResult(False, "no_wow", "WoW introuvable : choisis le dossier « World of Warcraft » "
                                              "dans la page WoW.")
    src = src or addon_source_dir()
    if src is None:
        return InstallResult(False, "no_source", "L'addon TreeAI n'est pas inclus dans cette version.")
    version = addon_version(src)
    dest = inst.addon_dir
    try:
        existed = dest.is_dir()
        if existed and not force and not needs_update(inst, src):
            return InstallResult(True, "up_to_date", f"Addon à jour{(' (' + version + ')') if version else ''}.",
                                 version, dest)
        inst.addons_dir.mkdir(parents=True, exist_ok=True)
        stamp = f"{os.getpid()}_{int(time.time() * 1000)}"
        tmp = inst.addons_dir / f".{ADDON_NAME}.new_{stamp}"
        old = inst.addons_dir / f".{ADDON_NAME}.old_{stamp}"
        shutil.copytree(src, tmp, ignore=shutil.ignore_patterns("*.pyc", "__pycache__", ".*"))
        if existed:
            os.replace(dest, old)
        try:
            os.replace(tmp, dest)
        except OSError:
            if existed:
                os.replace(old, dest)                # put the previous version back
            raise
        if existed:
            shutil.rmtree(old, ignore_errors=True)
        log.info("WoW addon %s %s in %s", version or "?", "updated" if existed else "installed", dest)
        msg = ("Addon mis à jour" if existed else "Addon installé") + (f" ({version})" if version else "")
        return InstallResult(True, "updated" if existed else "installed",
                             msg + ". En jeu : /reload (ou relance WoW), puis /tai.", version, dest)
    except Exception as exc:
        log.debug("addon install failed", exc_info=True)
        for leftover in inst.addons_dir.glob(f".{ADDON_NAME}.new_*") if inst.addons_dir.is_dir() else ():
            shutil.rmtree(leftover, ignore_errors=True)
        hint = " Ferme WoW ou lance TreeAI en administrateur." if isinstance(exc, PermissionError) else ""
        return InstallResult(False, "error", f"Installation impossible : {type(exc).__name__}.{hint}",
                             version, dest)


# ------------------------------------------------------------------ the game's own files
def savedvars_files(inst: WowInstall | None, name: str = ADDON_NAME) -> list[Path]:
    """``WTF/Account/*/SavedVariables/<name>.lua`` and per-character copies, newest first."""
    if inst is None:
        return []
    wtf = inst.flavor_dir / "WTF" / "Account"
    out: list[Path] = []
    try:
        if wtf.is_dir():
            out += list(wtf.glob(f"*/SavedVariables/{name}.lua"))
            out += list(wtf.glob(f"*/*/*/SavedVariables/{name}.lua"))
        out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        log.debug("savedvars scan failed", exc_info=True)
    return out


def file_status(path: Path | None, now: float | None = None) -> dict:
    """{exists, size, age_s} of a game file (combat log...). Never raises."""
    try:
        if path is not None and path.is_file():
            st = path.stat()
            return {"exists": True, "size": int(st.st_size),
                    "age_s": max(0.0, (time.time() if now is None else now) - st.st_mtime)}
    except OSError:
        pass
    return {"exists": False, "size": 0, "age_s": None}


def combat_log_status(inst: WowInstall | None, now: float | None = None) -> tuple[str, str]:
    """(tone, French text) for the combat log: "ok" if written in the last 10 min, else a tip."""
    if inst is None:
        return "", "WoW introuvable."
    st = file_status(inst.combat_log, now)
    if not st["exists"]:
        return "warning", "Pas encore de journal de combat. " + COMBATLOG_TIP
    age = st["age_s"] or 0.0
    if age < 600:
        return "ok", f"Journal de combat actif ({st['size'] // 1024} Ko)."
    return "warning", f"Journal de combat inactif depuis {int(age // 60)} min. Tape /combatlog en jeu."
