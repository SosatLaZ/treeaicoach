"""Bring a built TreeAI Coach app folder (PyInstaller one-dir) up to date with the source, without a rebuild.

    python packaging/patch_app.py <app_dir> [--repo <root>] [--check]

``<app_dir>`` holds ``TreeAICoach-app.exe`` and ``_internal/`` (the previous release unzipped, or a fresh build).

1. Every module of ``treeaicoach/`` is compiled from the source into the exe's PYZ archive: existing ones are
   replaced, missing ones added, the ones that no longer exist in the source removed. Never a hand-made list:
   3.7 → 3.9.9 shipped with only the modules of a git diff, so ``wow.errlog``, ``wow.stats``, ``planner.speed``
   and ``planner.transport`` were missing in the app (Session tab empty, « Calculer » broken).
2. Package data (every non-``.py`` file under ``treeaicoach/``, e.g. ``wow/data/pets.json.gz``) is mirrored into
   ``_internal/treeaicoach/`` (files that left the source are deleted).
3. ``_internal/wow_addon/`` is replaced wholesale by ``wow_addon/TreeAI`` + ``wow_addon/TUTO_FR.md`` (the app
   installs whatever sits there in the player's ``Interface/AddOns``; removed addon files must not stay).
4. Verification, also alone with ``--check`` (nothing written): each module's code in the exe is exactly
   ``compile(source)`` (marshal bytes; a build by PyInstaller itself is compared code object by code object),
   no module missing or extra; every import of a ``treeaicoach`` module resolves in the frozen PYZ; every other
   import is found in the frozen app (PYZ, ``base_library.zip``, extension modules of ``_internal``, built-ins)
   or listed (an unguarded standard-library module that is not bundled is an error: rebuild with PyInstaller);
   data files and the addon identical; the entry script still equals ``packaging/launcher.py``.

Exit code 0 when everything matches, 1 on any mismatch, 2 when the folder or the exe cannot be read. Standard
library only (PyInstaller's archive reader, when installed, re-reads the patched exe as a cross-check). Run it
with the frozen app's Python version (3.11): bytecode is version-specific, the tool refuses another one.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import marshal
import os
import shutil
import struct
import sys
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

APP_EXE = "TreeAICoach-app.exe"
CONTENTS = "_internal"                     # PyInstaller >= 6 one-dir: everything but the exe lives here
PKG = "treeaicoach"
SKIP_MODULES = frozenset({"treeaicoach.__main__"})   # packaging/treeaicoach.spec discards it too
ADDON_ITEMS = ("TreeAI", "TUTO_FR.md")     # what the app ships of wow_addon/ (install.addon_source_dir)
SCRIPT = ("launcher", "packaging/launcher.py")       # the exe's entry script (PKG entry, not in the PYZ)
OPTIMIZE = 0                               # packaging/treeaicoach.spec: Analysis(optimize=0)
JUNK_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
PYZ_MODULE, PYZ_PKG, PYZ_NSPKG = 0, 1, 3
PYZ_HEADER = 17                            # PyInstaller's ZlibArchiveWriter._HEADER_LENGTH
COMPRESS_LEVEL = 6                         # PyInstaller's ZlibArchiveWriter._COMPRESSION_LEVEL
#: sys.builtin_module_names of CPython 3.11 on Windows (compiled into python311.dll, not files of _internal).
WIN_BUILTINS = frozenset("""_abc _ast _bisect _blake2 _codecs _codecs_cn _codecs_hk _codecs_iso2022 _codecs_jp
_codecs_kr _codecs_tw _collections _contextvars _csv _datetime _functools _heapq _imp _io _json _locale _lsprof _md5
_multibytecodec _opcode _operator _pickle _random _sha1 _sha256 _sha3 _sha512 _signal _sre _stat _statistics _string
_struct _symtable _thread _tokenize _tracemalloc _typing _warnings _weakref _winapi _xxsubinterpreters array atexit
audioop binascii builtins cmath errno faulthandler gc itertools marshal math mmap msvcrt nt sys time winreg xxsubtype
zlib _frozen_importlib _frozen_importlib_external zipimport""".split())
_GUARD_EXC = frozenset({"ImportError", "ModuleNotFoundError", "Exception", "BaseException"})

try:                                       # the formats of PyInstaller's CArchive (PKG); fixed since PyInstaller 6
    from PyInstaller.archive.readers import CArchiveReader as _Reader

    COOKIE_MAGIC, COOKIE_FMT, TOC_FMT = _Reader._COOKIE_MAGIC_PATTERN, _Reader._COOKIE_FORMAT, _Reader._TOC_ENTRY_FORMAT
except Exception:                          # PyInstaller not installed: same constants, no cross-check
    _Reader = None
    COOKIE_MAGIC, COOKIE_FMT, TOC_FMT = b"MEI\014\013\012\013\016", "!8sIIII64s", "!IIIIBc"
COOKIE_LEN, TOC_LEN = struct.calcsize(COOKIE_FMT), struct.calcsize(TOC_FMT)


class PatchError(Exception):
    """The folder / exe cannot be patched (exit code 2)."""


# ------------------------------------------------------------------------------------------- the exe (PKG)
@dataclass
class PkgEntry:
    offset: int
    length: int
    ulength: int
    cflag: int
    typecode: bytes
    raw_name: bytes                        # NUL-padded name, kept as is (16-byte alignment of the TOC)

    @property
    def name(self) -> str:
        return self.raw_name.rstrip(b"\0").decode("utf-8")


class Exe:
    """The bootloader exe with its CArchive (PKG) appended: [bootloader][blobs…][TOC][cookie][tail]."""

    def __init__(self, path: Path) -> None:
        self.path = path
        data = path.read_bytes()
        cpos = data.rfind(COOKIE_MAGIC)
        if cpos < 0:
            raise PatchError(f"{path.name} : pas d'archive PyInstaller (cookie introuvable)")
        magic, pkg_len, toc_off, toc_len, self.pyvers, self.pylib = struct.unpack(
            COOKIE_FMT, data[cpos:cpos + COOKIE_LEN])
        self.magic = magic
        start = cpos + COOKIE_LEN - pkg_len
        if start < 0 or toc_off + toc_len > pkg_len:
            raise PatchError(f"{path.name} : archive PyInstaller illisible")
        self.prefix = data[:start]
        self.tail = data[cpos + COOKIE_LEN:]       # normally empty (an Authenticode signature would be lost)
        self.entries: list[PkgEntry] = []
        toc = data[start + toc_off:start + toc_off + toc_len]
        p = 0
        while p < len(toc):
            el, off, ln, uln, cf, tc = struct.unpack(TOC_FMT, toc[p:p + TOC_LEN])
            if el < TOC_LEN or p + el > len(toc):
                raise PatchError(f"{path.name} : table des matières PyInstaller abîmée")
            self.entries.append(PkgEntry(off, ln, uln, cf, tc, toc[p + TOC_LEN:p + el]))
            p += el
        self.blobs = {id(e): data[start + e.offset:start + e.offset + e.length] for e in self.entries}
        pos = 0                                     # the blobs must tile [0, toc_off): rebuilt in that order
        for e in sorted(self.entries, key=lambda e: e.offset):
            if e.offset != pos:
                raise PatchError(f"{path.name} : agencement inattendu de l'archive ({e.name} à {e.offset}, attendu {pos})")
            pos += e.length
        if pos != toc_off:
            raise PatchError(f"{path.name} : agencement inattendu de l'archive (fin des données {pos} ≠ {toc_off})")

    def entry(self, name: str) -> PkgEntry | None:
        return next((e for e in self.entries if e.name == name), None)

    def data(self, e: PkgEntry) -> bytes:
        raw = self.blobs[id(e)]
        return zlib.decompress(raw) if e.cflag else raw

    def pyz_entry(self) -> PkgEntry:
        e = next((e for e in self.entries if e.typecode == b"z"), None)
        if e is None:
            raise PatchError(f"{self.path.name} : pas d'archive PYZ dans l'exe")
        return e

    def rebuilt(self, replace: dict[str, bytes]) -> bytes:
        """The exe with the (uncompressed) blobs of ``replace`` swapped in; everything else byte for byte."""
        body, newpos = bytearray(), {}
        for e in sorted(self.entries, key=lambda e: e.offset):
            blob = replace.get(e.name)
            if blob is None:
                blob = self.blobs[id(e)]
            newpos[id(e)] = (len(body), len(blob))
            body += blob
        toc = bytearray()
        for e in self.entries:
            off, ln = newpos[id(e)]
            uln, cf = (ln, 0) if e.name in replace else (e.ulength, e.cflag)
            toc += struct.pack(TOC_FMT, TOC_LEN + len(e.raw_name), off, ln, uln, cf, e.typecode) + e.raw_name
        cookie = struct.pack(COOKIE_FMT, self.magic, len(body) + len(toc) + COOKIE_LEN, len(body), len(toc),
                             self.pyvers, self.pylib)
        return self.prefix + bytes(body) + bytes(toc) + cookie + self.tail


# ------------------------------------------------------------------------------------------- the PYZ
class Pyz:
    """PyInstaller's PYZ: b"PYZ\\0" + bytecode magic + TOC offset (+ reserved bytes), zlib'd marshalled code
    objects, then the marshalled TOC [(name, (typecode, offset, length))]."""

    def __init__(self, blob: bytes) -> None:
        if blob[:4] != b"PYZ\0":
            raise PatchError("archive PYZ illisible (signature)")
        self.pymagic = blob[4:8]
        toc_off = struct.unpack("!i", blob[8:12])[0]
        toc = marshal.loads(blob[toc_off:])
        self.toc_is_dict = isinstance(toc, dict)
        items = list(toc.items()) if self.toc_is_dict else list(toc)
        self.entries: dict[str, tuple[int, bytes]] = {}
        for name, (typ, off, ln) in items:
            self.entries[name] = (typ, blob[off:off + ln])
        start = min((off for _n, (_t, off, _l) in items), default=PYZ_HEADER)
        self.reserved = blob[12:start] if start >= 12 else b""

    def code_bytes(self, name: str) -> bytes | None:
        e = self.entries.get(name)
        return zlib.decompress(e[1]) if e is not None else None

    def build(self) -> bytes:
        head = 12 + len(self.reserved)
        body, toc = bytearray(), []
        for name, (typ, raw) in self.entries.items():
            toc.append((name, (typ, head + len(body), len(raw))))
            body += raw
        tocb = marshal.dumps(dict(toc) if self.toc_is_dict else toc)
        return b"PYZ\0" + self.pymagic + struct.pack("!i", head + len(body)) + self.reserved + bytes(body) + tocb


# ------------------------------------------------------------------------------------------- the source
@dataclass
class SourceModule:
    name: str
    path: Path
    is_pkg: bool

    def rel_parts(self) -> list[str]:
        parts = self.name.split(".")
        return parts + ["__init__.py"] if self.is_pkg else parts[:-1] + [parts[-1] + ".py"]


def source_modules(repo: Path) -> dict[str, SourceModule]:
    """Every importable module under ``<repo>/treeaicoach`` (package dirs only: the PYZ has no namespace packages)."""
    base = repo / PKG
    if not (base / "__init__.py").is_file():
        raise PatchError(f"{base} : paquet treeaicoach introuvable (--repo ?)")
    out: dict[str, SourceModule] = {}
    for p in sorted(base.rglob("*.py")):
        rel = p.relative_to(repo)
        if "__pycache__" in rel.parts or any(part.startswith(".") for part in rel.parts):
            continue
        parts = list(rel.with_suffix("").parts)
        is_pkg = parts[-1] == "__init__"
        if is_pkg:
            parts = parts[:-1]
        if not all(x.isidentifier() for x in parts):
            continue
        if not all((repo.joinpath(*parts[:i]) / "__init__.py").is_file() for i in range(1, len(parts))):
            continue                                  # a .py in a folder without __init__.py: not part of the package
        name = ".".join(parts)
        if name not in SKIP_MODULES:
            out[name] = SourceModule(name, p, is_pkg)
    return out


def code_filename(mod: SourceModule, sep: str = "\\") -> str:
    """What PyInstaller stores as co_filename: the path relative to the package root, OS separators."""
    return sep.join(mod.rel_parts())


def compile_module(mod: SourceModule, filename: str):  # noqa: ANN201 (code object)
    return compile(mod.path.read_bytes(), filename, "exec", dont_inherit=True, optimize=OPTIMIZE)


def module_bytes(mod: SourceModule, filename: str) -> bytes:
    """The exact bytes stored in the PYZ (before zlib). marshal sets its reference flag on objects referenced more
    than once, so the code object stays a temporary here, for the patch and for the check alike."""
    return marshal.dumps(compile_module(mod, filename))


def data_files(base: Path) -> dict[str, Path]:
    """{relative posix path: file} of the package data: every non-Python file, no caches or OS litter."""
    out: dict[str, Path] = {}
    if not base.is_dir():
        return out
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(base)
        if "__pycache__" in rel.parts or p.suffix in (".py", ".pyc", ".pyo") or p.name in JUNK_NAMES:
            continue
        out[rel.as_posix()] = p
    return out


def addon_files(repo: Path) -> dict[str, Path]:
    """{path relative to _internal/wow_addon: file} of what the app ships of wow_addon/."""
    src = repo / "wow_addon"
    out: dict[str, Path] = {}
    for item in ADDON_ITEMS:
        p = src / item
        if p.is_file():
            out[item] = p
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                rel = f.relative_to(src)
                if f.is_file() and not any(x.startswith(".") or x == "__pycache__" for x in rel.parts) \
                        and f.suffix != ".pyc":
                    out[rel.as_posix()] = f
    return out


def tree_files(base: Path) -> dict[str, Path]:
    if not base.is_dir():
        return {}
    return {p.relative_to(base).as_posix(): p for p in sorted(base.rglob("*")) if p.is_file()}


def same_file(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return _sha(a) == _sha(b)
    except OSError:
        return False


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------------------------------- imports
@dataclass
class ImportUse:
    module: str          # importing module
    line: int
    target: str          # absolute dotted name
    guarded: bool        # inside try/except ImportError, `if TYPE_CHECKING`, or a platform test
    from_names: tuple[str, ...] = ()


def _guarded(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    cur = node
    while id(cur) in parents:
        par = parents[id(cur)]
        if isinstance(par, ast.Try) and any(cur is s for s in par.body):
            for h in par.handlers:
                names: list[str] = []
                t = h.type
                for x in (t.elts if isinstance(t, ast.Tuple) else [t]) if t is not None else []:
                    names.append(x.id if isinstance(x, ast.Name) else x.attr if isinstance(x, ast.Attribute) else "")
                if t is None or _GUARD_EXC.intersection(names):
                    return True
        if isinstance(par, ast.If) and any(cur is s for s in par.body):
            test = ast.unparse(par.test)
            if "TYPE_CHECKING" in test or "sys.platform" in test or "os.name" in test or "platform.system" in test:
                return True
        cur = par
    return False


def module_imports(mod: SourceModule) -> list[ImportUse]:
    tree = ast.parse(mod.path.read_bytes(), filename=str(mod.path))
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    pkg = mod.name if mod.is_pkg else mod.name.rpartition(".")[0]
    out: list[ImportUse] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            g = _guarded(node, parents)
            out += [ImportUse(mod.name, node.lineno, a.name, g) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg.split(".")
                base = base[:len(base) - node.level + 1]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            out.append(ImportUse(mod.name, node.lineno, target, _guarded(node, parents),
                                 tuple(a.name for a in node.names if a.name != "*")))
    return out


def _top_level_names(path: Path) -> set[str]:
    """Names a module defines at its top level (assignments, defs, imports): « from pkg import x » attributes."""
    try:
        tree = ast.parse(path.read_bytes())
    except (OSError, SyntaxError):
        return set()
    names: set[str] = set()
    for node in tree.body:
        for n in ast.walk(node) if isinstance(node, (ast.If, ast.Try)) else [node]:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(n.name)
            elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                    names |= {x.id for x in ast.walk(t) if isinstance(x, ast.Name)}
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                names |= {(a.asname or a.name).split(".")[0] for a in n.names}
    return names


class FrozenModules:
    """What ``import`` finds in the frozen app: the PYZ, base_library.zip, files of _internal, built-ins."""

    def __init__(self, pyz: Pyz, internal: Path, exe: Exe | None = None) -> None:
        self.pkgs: set[str] = {n for n, (t, _r) in pyz.entries.items() if t in (PYZ_PKG, PYZ_NSPKG)}
        self.names: set[str] = set(pyz.entries) | set(WIN_BUILTINS)
        for e in exe.entries if exe is not None else ():     # bootstrap modules of the PKG (struct, pyimod0x…)
            if e.typecode in (b"m", b"M"):
                self.names.add(e.name)
                if e.typecode == b"M":
                    self.pkgs.add(e.name)
        bl = internal / "base_library.zip"
        if bl.is_file():
            with zipfile.ZipFile(bl) as z:
                for n in z.namelist():
                    if n.endswith((".pyc", ".py")):
                        self._add(n.rsplit(".", 1)[0].split("/"))
        for p in internal.rglob("*"):
            suf = p.suffix.lower()
            if suf in (".pyd", ".so") or (suf in (".py", ".pyc") and p.is_file()):
                rel = list(p.relative_to(internal).parts)
                if any(not x.split(".")[0].isidentifier() for x in rel[:-1]):
                    continue
                rel[-1] = rel[-1].split(".")[0]
                if rel and rel[0] == PKG:
                    continue                          # stray files of the package dir never count
                self._add(rel)

    def _add(self, parts: list[str]) -> None:
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
            self.pkgs.add(".".join(parts))
        if parts:
            self.names.add(".".join(parts))

    def has(self, name: str) -> bool:
        if name in self.names:
            return True
        parent = name.rpartition(".")[0]          # os.path: an attribute of a plain module
        return bool(parent) and parent in self.names and parent not in self.pkgs


def _is_stdlib(name: str) -> bool:
    return name.split(".")[0] in getattr(sys, "stdlib_module_names", ()) or name.split(".")[0] in WIN_BUILTINS


def _stdlib_submodule(parent: str, attr: str) -> bool:
    """« from <stdlib package> import <attr> »: is <attr> a submodule (on this interpreter)?"""
    try:
        spec = importlib.util.find_spec(parent)
        if spec is None or not spec.submodule_search_locations:
            return False
        return importlib.util.find_spec(f"{parent}.{attr}") is not None
    except Exception:
        return False


@dataclass
class ImportReport:
    checked: int = 0
    pkg_missing: list[str] = field(default_factory=list)        # unguarded treeaicoach imports that do not resolve
    std_missing: list[str] = field(default_factory=list)        # unguarded stdlib imports not bundled
    other_missing: list[str] = field(default_factory=list)      # unguarded third-party imports not bundled
    optional: dict[str, set[str]] = field(default_factory=dict)  # guarded, not bundled: name -> importers


def check_imports(mods: dict[str, SourceModule], frozen: FrozenModules) -> ImportReport:
    rep = ImportReport()
    pkg_attrs: dict[str, set[str]] = {}
    for m in mods.values():
        for use in module_imports(m):
            targets = [use.target]
            for a in use.from_names:
                sub = f"{use.target}.{a}"
                if use.target == PKG or use.target.startswith(PKG + "."):
                    owner = mods.get(use.target)
                    if sub in mods or sub in frozen.names:
                        targets.append(sub)
                    elif owner is not None and owner.is_pkg:
                        attrs = pkg_attrs.setdefault(owner.name, _top_level_names(owner.path))
                        if a not in attrs:
                            targets.append(sub)       # not an attribute of the package: must be a submodule
                elif sub in frozen.names or (_is_stdlib(use.target) and use.target in frozen.pkgs
                                             and _stdlib_submodule(use.target, a)):
                    targets.append(sub)
            for t in targets:
                if not t:
                    continue
                rep.checked += 1
                where = f"{t} ({use.module}:{use.line})"
                if t == PKG or t.startswith(PKG + "."):
                    ok = t in mods and frozen.has(t)
                    if not ok and not use.guarded:
                        rep.pkg_missing.append(where)
                    elif not ok:
                        rep.optional.setdefault(t, set()).add(use.module)
                    continue
                if frozen.has(t):
                    continue
                if use.guarded:
                    rep.optional.setdefault(t, set()).add(use.module)
                elif _is_stdlib(t):
                    rep.std_missing.append(where)
                else:
                    rep.other_missing.append(where)
    return rep


# ------------------------------------------------------------------------------------------- patch
@dataclass
class Result:
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def bad(self, text: str) -> None:
        self.problems.append(text)


def _sep_of(pyz: Pyz) -> str:
    for name in (PKG, "treeaicoach.main"):
        raw = pyz.code_bytes(name)
        if raw is not None:
            fn = marshal.loads(raw).co_filename
            return "\\" if "\\" in fn else "/"
    return "\\"


def patch_pyz(pyz: Pyz, mods: dict[str, SourceModule]) -> tuple[int, int, list[str]]:
    """Every source module compiled into ``pyz`` (in place). (replaced, added, removed names)."""
    sep = _sep_of(pyz)
    replaced = added = 0
    removed = [n for n in pyz.entries if (n == PKG or n.startswith(PKG + ".")) and n not in mods]
    for n in removed:
        del pyz.entries[n]
    for name, m in sorted(mods.items()):
        old = pyz.code_bytes(name)
        fn = marshal.loads(old).co_filename if old is not None else code_filename(m, sep)
        raw = zlib.compress(module_bytes(m, fn), COMPRESS_LEVEL)
        if old is None:
            added += 1
        else:
            replaced += 1
        pyz.entries[name] = (PYZ_PKG if m.is_pkg else PYZ_MODULE, raw)
    return replaced, added, removed


def sync_tree(want: dict[str, Path], dest: Path, wholesale: bool) -> tuple[int, int]:
    """Make ``dest`` hold exactly ``want`` ({relative path: source file}). (copied, removed)."""
    if wholesale:
        tmp = dest.with_name(f".{dest.name}.new_{os.getpid()}")
        old = dest.with_name(f".{dest.name}.old_{os.getpid()}")
        shutil.rmtree(tmp, ignore_errors=True)
        for rel, src in want.items():
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tmp / rel)
        removed = len(tree_files(dest))
        if dest.exists():
            os.replace(dest, old)
        os.replace(tmp, dest)
        shutil.rmtree(old, ignore_errors=True)
        return len(want), removed
    have = tree_files(dest)
    copied = removed = 0
    for rel, p in have.items():
        if rel not in want:
            p.unlink()
            removed += 1
    for rel, src in want.items():
        tgt = dest / rel
        if rel in have and same_file(src, tgt):
            continue
        tgt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, tgt)
        copied += 1
    for d in sorted((p for p in dest.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)) if dest.is_dir() else []:
        if not any(d.iterdir()):
            d.rmdir()
    return copied, removed


def verify(app: Path, repo: Path, res: Result) -> None:
    """Checks 4 of the module doc: modules, imports, data, addon, entry script."""
    exe = Exe(app / APP_EXE)
    pyz = Pyz(exe.data(exe.pyz_entry()))
    mods = source_modules(repo)
    identical = same_code = 0
    changed, missing = [], []
    for name, m in sorted(mods.items()):
        raw = pyz.code_bytes(name)
        if raw is None:
            missing.append(name)
            continue
        typ = pyz.entries[name][0]
        if typ != (PYZ_PKG if m.is_pkg else PYZ_MODULE):
            changed.append(f"{name} (type {typ})")
            continue
        co = marshal.loads(raw)
        if co.co_filename.replace("\\", "/") != "/".join(m.rel_parts()):
            changed.append(f"{name} (fichier {co.co_filename})")
            continue
        if module_bytes(m, co.co_filename) == raw:
            identical += 1
        elif compile_module(m, co.co_filename) == co:   # PyInstaller's own build: same code, other marshal flags
            same_code += 1
        else:
            changed.append(name)
    extra = sorted(n for n in pyz.entries if (n == PKG or n.startswith(PKG + ".")) and n not in mods)
    res.notes.append(f"modules : {len(mods)} du source · {identical} identiques octet pour octet"
                     + (f", {same_code} identiques (code)" if same_code else "")
                     + f" · {len(changed)} différents · {len(missing)} absents · {len(extra)} en trop")
    for label, names in (("module différent du source", changed), ("module absent de l'exe", missing),
                         ("module en trop dans l'exe", extra)):
        for n in names:
            res.bad(f"{label} : {n}")
    if _Reader is not None:               # PyInstaller's own reader sees the same thing (cross-check)
        try:
            z = _Reader(str(app / APP_EXE)).open_embedded_archive(exe.pyz_entry().name)
            for name in mods:
                if name in pyz.entries and z.extract(name) is None:
                    res.bad(f"lecteur PyInstaller : {name} illisible")
        except Exception as exc:          # noqa: BLE001
            res.bad(f"lecteur PyInstaller : exe illisible ({type(exc).__name__}: {exc})")

    internal = app / CONTENTS
    frozen = FrozenModules(pyz, internal, exe)
    rep = check_imports(mods, frozen)
    opt = ", ".join(sorted(rep.optional))
    res.notes.append(f"imports : {rep.checked} vérifiés · treeaicoach non résolus {len(rep.pkg_missing)} · "
                     f"stdlib absente {len(rep.std_missing)} · autres absents {len(rep.other_missing)}"
                     + (f" · optionnels non embarqués (try/except) : {opt}" if opt else ""))
    for w in rep.pkg_missing:
        res.bad(f"import treeaicoach non résolu : {w}")
    for w in rep.std_missing:
        res.bad(f"module standard absent de l'app figée (refaire le build PyInstaller) : {w}")
    for w in rep.other_missing:
        res.notes.append(f"  attention, paquet absent de l'app figée (import sans try) : {w}")

    want = data_files(repo / PKG)
    have = tree_files(internal / PKG)
    diff = [r for r in want if r not in have or not same_file(want[r], have[r])]
    extra_d = [r for r in have if r not in want]
    res.notes.append(f"données : {len(want)} fichiers · {len(diff)} différents ou absents · {len(extra_d)} en trop")
    for r in diff:
        res.bad(f"donnée différente ou absente : _internal/{PKG}/{r}")
    for r in extra_d:
        res.bad(f"donnée en trop : _internal/{PKG}/{r}")

    want = addon_files(repo)
    have = tree_files(internal / "wow_addon")
    diff = [r for r in want if r not in have or not same_file(want[r], have[r])]
    extra_a = [r for r in have if r not in want]
    res.notes.append(f"addon : {len(want)} fichiers · {len(diff)} différents ou absents · {len(extra_a)} en trop")
    for r in diff:
        res.bad(f"fichier d'addon différent ou absent : _internal/wow_addon/{r}")
    for r in extra_a:
        res.bad(f"fichier d'addon en trop : _internal/wow_addon/{r}")

    name, rel = SCRIPT
    e, src = exe.entry(name), repo / rel
    if e is not None and src.is_file():
        co = marshal.loads(exe.data(e))
        if compile(src.read_bytes(), co.co_filename, "exec", dont_inherit=True, optimize=OPTIMIZE) != co:
            res.bad(f"script d'entrée {name} ≠ {rel} : refaire le build PyInstaller")


def patch(app: Path, repo: Path, res: Result) -> None:
    exe_path = app / APP_EXE
    exe = Exe(exe_path)
    pe = exe.pyz_entry()
    pyz = Pyz(exe.data(pe))
    if pyz.pymagic != importlib.util.MAGIC_NUMBER:
        frozen = f"{exe.pyvers // 100}.{exe.pyvers % 100}"
        raise PatchError(f"Python {sys.version_info[0]}.{sys.version_info[1]} ne compile pas pour l'app figée "
                         f"(Python {frozen}) : lance l'outil avec Python {frozen}")
    mods = source_modules(repo)
    replaced, added, removed = patch_pyz(pyz, mods)
    data = exe.rebuilt({pe.name: pyz.build()})
    tmp = exe_path.with_name(exe_path.name + ".new")
    tmp.write_bytes(data)
    back = Exe(tmp)                                # re-read before replacing the real exe
    if set(Pyz(back.data(back.pyz_entry())).entries) != set(pyz.entries):
        raise PatchError("exe réécrit illisible")
    os.replace(tmp, exe_path)
    res.notes.append(f"exe : {replaced} modules remplacés, {added} ajoutés, {len(removed)} retirés"
                     + (f" ({', '.join(removed)})" if removed else "") + f" · {len(data) / 1e6:.1f} Mo"
                     + (" · attention : octets après l'archive conservés (signature invalide)" if exe.tail else ""))
    internal = app / CONTENTS
    data, have = data_files(repo / PKG), tree_files(internal / PKG)
    gone = [r for r in have if r not in data]
    if not data or len(gone) > max(20, len(have) // 4):   # an incomplete checkout must not wipe the app's assets
        raise PatchError(f"{len(gone)} fichiers de données sur {len(have)} disparaîtraient : dépôt incomplet ?")
    if "TreeAI/TreeAI.toc" not in addon_files(repo):
        raise PatchError(f"{repo / 'wow_addon' / 'TreeAI' / 'TreeAI.toc'} introuvable")
    c, r = sync_tree(data, internal / PKG, wholesale=False)
    res.notes.append(f"données : {c} copiées, {r} supprimées dans _internal/{PKG}")
    c, r = sync_tree(addon_files(repo), internal / "wow_addon", wholesale=True)
    res.notes.append(f"addon : _internal/wow_addon remplacé ({c} fichiers, {r} anciens retirés)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("app", type=Path, help=f"dossier one-dir (contient {APP_EXE} et {CONTENTS}/)")
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1], help="racine du dépôt")
    ap.add_argument("--check", action="store_true", help="vérifier seulement (rien n'est écrit)")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):           # a cp1252 Windows console must not crash on « ✗ »
        try:
            stream.reconfigure(errors="replace")
        except Exception:                              # noqa: BLE001
            pass
    app, repo = a.app.resolve(), a.repo.resolve()
    t0 = time.monotonic()
    res = Result()
    try:
        if not (app / APP_EXE).is_file() or not (app / CONTENTS).is_dir():
            raise PatchError(f"{app} : {APP_EXE} ou {CONTENTS}/ introuvable")
        if not a.check:
            patch(app, repo, res)
        verify(app, repo, res)
    except PatchError as exc:
        print(f"patch_app : ERREUR : {exc}", file=sys.stderr)
        return 2
    print(f"patch_app : {app} ← {repo}" + (" (vérification seule)" if a.check else ""))
    for n in res.notes:
        print("  " + n)
    for p in res.problems:
        print("  ✗ " + p)
    verdict = "OK" if not res.problems else f"ÉCHEC ({len(res.problems)} problème{'s' if len(res.problems) > 1 else ''})"
    print(f"patch_app : {verdict} en {time.monotonic() - t0:.1f} s")
    return 0 if not res.problems else 1


if __name__ == "__main__":
    sys.exit(main())
