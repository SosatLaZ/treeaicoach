"""packaging/patch_app.py on a tiny synthetic one-dir app whose exe is written by PyInstaller's own archive writers:
a stale build is reported by --check, patched (every module of the source, data mirrored, addon replaced
wholesale), then verified; missing modules / imports fail the check. Skipped without PyInstaller."""

from __future__ import annotations

import importlib.util
import marshal
import py_compile
import sys
import zipfile
from pathlib import Path

import pytest

writers = pytest.importorskip("PyInstaller.archive.writers")
from PyInstaller.archive.readers import CArchiveReader  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BOOT = b"MZ" + b"\x90" * 300                      # stands for the bootloader part of the exe


def _tool():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("patch_app_under_test", ROOT / "packaging" / "patch_app.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                  # dataclasses look their module up while it executes
    spec.loader.exec_module(mod)
    return mod


P = _tool()


def _w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _repo(tmp: Path) -> Path:
    r = tmp / "repo"
    _w(r / "treeaicoach/__init__.py", '"""pkg"""\n__version__ = "4.0.0"\n')
    _w(r / "treeaicoach/__main__.py", "print('never frozen')\n")
    _w(r / "treeaicoach/wow/__init__.py", "def optional(name):\n    return None\n")
    _w(r / "treeaicoach/wow/live.py", "import json\nimport struct\nfrom treeaicoach.wow import stats\nVALUE = 2\n")
    _w(r / "treeaicoach/wow/stats.py", "import zlib\ntry:\n    import vosk\nexcept ImportError:\n    vosk = None\n")
    (r / "treeaicoach/wow/data").mkdir(parents=True)
    (r / "treeaicoach/wow/data/pets.json.gz").write_bytes(b"\x1f\x8b fake gzip")
    _w(r / "treeaicoach/assets/a.json", "{}")
    _w(r / "treeaicoach/wow/__pycache__/live.cpython-311.pyc", "cache, never shipped")
    _w(r / "wow_addon/TreeAI/TreeAI.toc", "## Version: 4.0.0\nCore.lua\n")
    _w(r / "wow_addon/TreeAI/Core.lua", "-- core 4.0\n")
    _w(r / "wow_addon/TreeAI/.DS_Store", "litter")
    _w(r / "wow_addon/TUTO_FR.md", "# Tuto\n")
    _w(r / "wow_addon/README.md", "API doc for developers, not shipped\n")
    _w(r / "packaging/launcher.py", "import sys\n\nfrom treeaicoach import wow\n")
    return r


def _app(tmp: Path, repo: Path) -> Path:
    """An older build: live.py v1, a module removed since, no stats.py / pets data, the old addon."""
    app, build = tmp / "app", tmp / "build"
    internal = app / "_internal"
    old = {"treeaicoach": ('"""pkg"""\n__version__ = "3.9.9"\n', True), "treeaicoach.wow": ("", True),
           "treeaicoach.wow.live": ("import json\nVALUE = 1\n", False), "treeaicoach.old": ("X = 1\n", False),
           "json": ("", True)}
    entries, code = [], {}
    for name, (src, pkg) in old.items():
        rel = name.replace(".", "\\") + ("\\__init__.py" if pkg else ".py")
        f = _w(build / rel.replace("\\", "/"), src)
        entries.append((name, str(f), "PYMODULE"))
        code[name] = compile(src, rel, "exec", dont_inherit=True)
    pyz = build / "PYZ.pyz"
    writers.ZlibArchiveWriter(str(pyz), entries, code)
    boot = _w(build / "struct_boot.py", "pack = None\n")
    pyc = Path(py_compile.compile(str(boot), cfile=str(build / "struct_boot.pyc")))
    pkg = build / "app.pkg"
    writers.CArchiveWriter(str(pkg), [("struct", str(pyc), True, "m"),
                                      ("launcher", str(repo / "packaging/launcher.py"), True, "s"),
                                      ("PYZ.pyz", str(pyz), False, "z"),
                                      ("pyi-contents-directory _internal", "", False, "o")], "python311.dll")
    internal.mkdir(parents=True)
    (app / P.APP_EXE).write_bytes(BOOT + pkg.read_bytes())
    with zipfile.ZipFile(internal / "base_library.zip", "w") as z:
        z.writestr("encodings/__init__.pyc", b"")
        z.writestr("os.pyc", b"")
    _w(internal / "treeaicoach/assets/a.json", "{}")
    _w(internal / "treeaicoach/old.dat", "stale data")
    _w(internal / "wow_addon/TreeAI/TreeAI.toc", "## Version: 3.9.9\nCore.lua\nModules\\Squad.lua\n")
    _w(internal / "wow_addon/TreeAI/Modules/Squad.lua", "-- removed in 4.0\n")
    _w(internal / "wow_addon/TUTO_FR.md", "# Ancien tuto\n")
    return app


@pytest.fixture()
def world(tmp_path: Path):  # noqa: ANN201
    repo = _repo(tmp_path)
    return repo, _app(tmp_path, repo)


def _run(capsys: pytest.CaptureFixture, *args: str) -> tuple[int, str]:
    rc = P.main([str(a) for a in args])
    return rc, capsys.readouterr().out


def test_check_reports_a_stale_build(world, capsys) -> None:  # noqa: ANN001
    repo, app = world
    before = (app / P.APP_EXE).read_bytes()
    rc, out = _run(capsys, app, "--repo", repo, "--check")
    assert rc == 1
    for text in ("module absent de l'exe : treeaicoach.wow.stats", "module en trop dans l'exe : treeaicoach.old",
                 "module différent du source : treeaicoach.wow.live",
                 "import treeaicoach non résolu : treeaicoach.wow.stats (treeaicoach.wow.live:3)",
                 "_internal/treeaicoach/wow/data/pets.json.gz", "donnée en trop : _internal/treeaicoach/old.dat",
                 "fichier d'addon en trop : _internal/wow_addon/TreeAI/Modules/Squad.lua"):
        assert text in out, text
    assert "PyInstaller) : struct" not in out                          # a bootstrap module of the PKG
    assert (app / P.APP_EXE).read_bytes() == before                   # --check writes nothing


def test_patch_then_check(world, capsys) -> None:  # noqa: ANN001
    repo, app = world
    rc, out = _run(capsys, app, "--repo", repo)
    assert rc == 0, out
    assert "modules : 4 du source · 4 identiques octet pour octet · 0 différents · 0 absents · 0 en trop" in out
    data = (app / P.APP_EXE).read_bytes()
    assert data.startswith(BOOT)                                       # the bootloader is untouched
    r = CArchiveReader(str(app / P.APP_EXE))                           # PyInstaller's reader agrees
    assert r.options == ["pyi-contents-directory _internal"]
    z = r.open_embedded_archive("PYZ.pyz")
    names = set(z.toc)
    assert {"treeaicoach", "treeaicoach.wow", "treeaicoach.wow.live", "treeaicoach.wow.stats", "json"} <= names
    assert "treeaicoach.old" not in names and "treeaicoach.__main__" not in names
    assert z.toc["treeaicoach.wow"][0] == 1 and z.toc["treeaicoach.wow.stats"][0] == 0
    st = z.extract("treeaicoach.wow.stats")
    assert st.co_filename == "treeaicoach\\wow\\stats.py"              # same style as PyInstaller's
    live = z.extract("treeaicoach.wow.live")
    assert 2 in live.co_consts and "stats" in live.co_names
    assert marshal.loads(r.extract("struct")).co_names == ("pack",)    # other PKG entries kept as they were
    internal = app / "_internal"
    assert (internal / "treeaicoach/wow/data/pets.json.gz").read_bytes() == b"\x1f\x8b fake gzip"
    assert not (internal / "treeaicoach/old.dat").exists() and (internal / "treeaicoach/assets/a.json").is_file()
    assert not list((internal / "treeaicoach").rglob("*.py*"))
    files = sorted(p.relative_to(internal / "wow_addon").as_posix() for p in (internal / "wow_addon").rglob("*")
                   if p.is_file())
    assert files == ["TUTO_FR.md", "TreeAI/Core.lua", "TreeAI/TreeAI.toc"]   # wholesale: Squad.lua gone
    assert not list(internal.glob(".wow_addon*")) and not (app / (P.APP_EXE + ".new")).exists()
    rc, out = _run(capsys, app, "--repo", repo, "--check")
    assert rc == 0, out
    _w(repo / "treeaicoach/wow/stats.py", "import zlib\nCHANGED = True\n")       # the source moved on
    rc, out = _run(capsys, app, "--repo", repo, "--check")
    assert rc == 1 and "module différent du source : treeaicoach.wow.stats" in out
    assert _run(capsys, app, "--repo", repo)[0] == 0                   # patching again is idempotent
    assert _run(capsys, app, "--repo", repo, "--check")[0] == 0


def test_imports_that_the_frozen_app_cannot_satisfy(world, capsys) -> None:  # noqa: ANN001
    repo, app = world
    _w(repo / "treeaicoach/wow/stats.py", "import tomllib\n")          # stdlib, but not in this frozen app
    rc, out = _run(capsys, app, "--repo", repo)
    assert rc == 1 and "module standard absent de l'app figée (refaire le build PyInstaller) : tomllib" in out
    _w(repo / "treeaicoach/wow/stats.py", "try:\n    import tomllib\nexcept ImportError:\n    tomllib = None\n")
    rc, out = _run(capsys, app, "--repo", repo)
    assert rc == 0 and "tomllib" in out                                 # guarded: listed, not an error
    _w(repo / "treeaicoach/wow/stats.py", "from treeaicoach.wow import nowhere\n")
    rc, out = _run(capsys, app, "--repo", repo)
    assert rc == 1 and "import treeaicoach non résolu : treeaicoach.wow.nowhere" in out
    _w(repo / "treeaicoach/wow/stats.py", "from treeaicoach.wow import optional\nimport os.path\n")
    assert _run(capsys, app, "--repo", repo)[0] == 0                   # a package attribute, os.path


def test_unusable_folder_or_checkout(world, tmp_path, capsys) -> None:  # noqa: ANN001
    repo, app = world
    assert P.main([str(tmp_path / "nothing"), "--repo", str(repo)]) == 2
    (app / P.APP_EXE).write_bytes(b"MZ not a PyInstaller exe")
    assert P.main([str(app), "--repo", str(repo), "--check"]) == 2


def test_real_source_tree() -> None:
    mods = P.source_modules(ROOT)
    for name in ("treeaicoach", "treeaicoach.wow.stats", "treeaicoach.wow.errlog", "treeaicoach.wow.planner.speed",
                 "treeaicoach.wow.planner.transport", "treeaicoach.wow.ai_facts"):
        assert name in mods
    assert "treeaicoach.__main__" not in mods and not any("__pycache__" in str(m.path) for m in mods.values())
    assert mods["treeaicoach.wow.planner"].is_pkg and P.code_filename(mods["treeaicoach.wow.planner"]) == \
        "treeaicoach\\wow\\planner\\__init__.py"
    data = P.data_files(ROOT / "treeaicoach")
    assert "wow/data/pets.json.gz" in data and not any(k.endswith(".py") for k in data)
    addon = P.addon_files(ROOT)
    assert "TreeAI/TreeAI.toc" in addon and "TUTO_FR.md" in addon and "README.md" not in addon
    assert not any(k.startswith("TreeAI_Plan") for k in addon)
