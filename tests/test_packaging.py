"""Tests for the delivery files: PyInstaller spec, version resource, icon, CI workflow, README.

Everything runs without PyInstaller (the spec is executed with stand-ins for Analysis/PYZ/EXE);
the checks that need the real PyInstaller helpers are skipped when it is not installed.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import struct
import sys
import types
from pathlib import Path
from typing import Any

import pytest

import treeaicoach

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
SPEC = PACKAGING / "treeaicoach.spec"
VERSION_FILE = PACKAGING / "version_info.txt"
WORKFLOW = ROOT / ".github" / "workflows" / "build-windows.yml"
BAT = PACKAGING / "build_exe.bat"
ICON_PNG = PACKAGING / "icon.png"
ICON_ICO = PACKAGING / "icon.ico"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

REQUIRED_HIDDEN = {"win32com", "win32com.client", "pythoncom", "pywintypes", "PIL._tkinter_finder",
                   "customtkinter", "onnxruntime"}
REQUIRED_EXCLUDES = {"torch", "torchvision", "onnx", "matplotlib", "scipy", "pandas", "pytest",
                     "IPython", "training"}
RUNTIME_DEPS = {"numpy", "cv2", "onnxruntime", "mss", "PIL", "customtkinter", "tkinter",
                "win32com", "pythoncom", "pywintypes", "treeaicoach"}
LEGAL_NOTICE = (
    "TreeAI Coach isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games "
    "or anyone officially involved in producing or managing Riot Games properties. Riot Games, and all "
    "associated properties are trademarks or registered trademarks of Riot Games, Inc."
)


# --------------------------------------------------------------------------- helpers
class _FakeAnalysis:
    """Stand-in for PyInstaller's Analysis: records its arguments, exposes TOC-like lists."""

    def __init__(self, scripts: list[str], **kwargs: Any) -> None:
        self.scripts_in = scripts
        self.kwargs = kwargs
        self.pure = [("treeaicoach", "treeaicoach/__init__.py", "PYMODULE")]
        self.scripts = [("launcher", scripts[0], "PYSOURCE")]
        self.binaries = [
            ("onnxruntime/capi/onnxruntime.dll", "C:/x/onnxruntime.dll", "BINARY"),
            ("cv2/opencv_videoio_ffmpeg4100_64.dll", "C:/x/opencv_videoio_ffmpeg4100_64.dll", "BINARY"),
        ]
        self.datas = [
            ("treeaicoach/assets/manifest.json", "C:/x/manifest.json", "DATA"),
            ("customtkinter/assets/.DS_Store", "C:/x/.DS_Store", "DATA"),
        ]


def _fake_hooks_module() -> types.ModuleType:
    hooks = types.ModuleType("PyInstaller.utils.hooks")
    hooks.collect_data_files = lambda pkg, **kw: [(f"/site/{pkg}/assets/themes/blue.json", f"{pkg}/assets/themes")]
    hooks.collect_submodules = lambda pkg, **kw: [pkg, f"{pkg}.paths"]
    hooks.collect_dynamic_libs = lambda pkg, **kw: []
    return hooks


def _exec_spec(monkeypatch: pytest.MonkeyPatch, fake_hooks: bool = True) -> dict[str, Any]:
    """Execute the spec like PyInstaller does, with recording stand-ins for the build classes."""
    calls: dict[str, Any] = {}

    def analysis(scripts: list[str], **kwargs: Any) -> _FakeAnalysis:
        calls["analysis"] = _FakeAnalysis(scripts, **kwargs)
        return calls["analysis"]

    def pyz(*args: Any, **kwargs: Any) -> str:
        calls["pyz"] = (args, kwargs)
        return "PYZ"

    def exe(*args: Any, **kwargs: Any) -> str:
        calls["exe"] = (args, kwargs)
        return "EXE"

    def collect(*args: Any, **kwargs: Any) -> str:  # must NOT be used: one-file build
        calls["collect"] = (args, kwargs)
        return "COLLECT"

    if fake_hooks:
        pkg = types.ModuleType("PyInstaller")
        utils = types.ModuleType("PyInstaller.utils")
        hooks = _fake_hooks_module()
        pkg.utils = utils  # type: ignore[attr-defined]
        utils.hooks = hooks  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "PyInstaller", pkg)
        monkeypatch.setitem(sys.modules, "PyInstaller.utils", utils)
        monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    monkeypatch.setattr(sys, "path", list(sys.path))  # the spec may prepend the repo root

    namespace: dict[str, Any] = {
        "__name__": "__main__", "__file__": str(SPEC), "SPEC": str(SPEC), "SPECPATH": str(PACKAGING),
        "Analysis": analysis, "PYZ": pyz, "EXE": exe, "COLLECT": collect,
    }
    exec(compile(SPEC.read_text(encoding="utf-8"), str(SPEC), "exec"), namespace)
    calls["namespace"] = namespace
    return calls


def _package_module_names() -> set[str]:
    names = {"treeaicoach"}
    for f in (ROOT / "treeaicoach").glob("*.py"):
        if f.stem not in ("__init__", "__main__"):
            names.add(f"treeaicoach.{f.stem}")
    return names


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    parts = [int(p) for p in re.findall(r"\d+", version)][:4]
    return tuple(parts + [0] * (4 - len(parts)))  # type: ignore[return-value]


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
        assert m, "version not found in pyproject.toml"
        return m.group(1)
    return tomllib.loads(text)["project"]["version"]


def _eval_version_file() -> Any:
    """Evaluate version_info.txt with inert constructors -> nested (name, args, kwargs) tuples."""
    names = ("VSVersionInfo", "FixedFileInfo", "StringFileInfo", "StringTable", "StringStruct",
             "VarFileInfo", "VarStruct")
    ns = {n: (lambda n: lambda *a, **kw: (n, a, kw))(n) for n in names}
    return eval(VERSION_FILE.read_text(encoding="utf-8"), {"__builtins__": {}}, ns)


def _walk(node: Any, name: str) -> list[tuple[tuple, dict]]:
    found: list[tuple[tuple, dict]] = []
    if isinstance(node, tuple) and len(node) == 3 and isinstance(node[0], str) and isinstance(node[2], dict):
        if node[0] == name:
            found.append((node[1], node[2]))
        for child in list(node[1]) + list(node[2].values()):
            found += _walk(child, name)
    elif isinstance(node, (list, tuple)):
        for child in node:
            found += _walk(child, name)
    return found


def _load_make_icon() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("treeaicoach_make_icon", PACKAGING / "make_icon.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses need the module registered
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return mod


def _ico_entries(data: bytes) -> list[dict[str, int]]:
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert reserved == 0 and kind == 1, "not an .ico file"
    out = []
    for i in range(count):
        w, h, _colors, _res, planes, bpp, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        out.append({"w": w or 256, "h": h or 256, "planes": planes, "bpp": bpp, "size": size, "offset": offset})
    return out


# --------------------------------------------------------------------------- spec
def test_spec_is_valid_python():
    ast.parse(SPEC.read_text(encoding="utf-8"), filename=str(SPEC))
    ast.parse((PACKAGING / "launcher.py").read_text(encoding="utf-8"))


def test_spec_builds_one_file_windowed_exe(monkeypatch):
    calls = _exec_spec(monkeypatch)
    assert "collect" not in calls, "one-file build: no COLLECT step"
    a: _FakeAnalysis = calls["analysis"]
    args, kw = calls["exe"]
    assert kw["name"] == "TreeAICoach"
    assert kw["console"] is False
    assert kw["upx"] is False
    assert kw.get("debug", False) is False
    icon = kw["icon"] if isinstance(kw["icon"], str) else kw["icon"][0]
    assert Path(icon).resolve() == ICON_ICO.resolve()
    assert Path(kw["version"]).resolve() == VERSION_FILE.resolve()
    # one-file: binaries and datas are passed to EXE itself
    assert a.binaries in args and a.datas in args and a.scripts in args
    assert not kw.get("exclude_binaries", False)


def test_spec_paths_are_absolute_and_exist(monkeypatch):
    calls = _exec_spec(monkeypatch)
    a: _FakeAnalysis = calls["analysis"]
    entry = Path(a.scripts_in[0])
    assert entry.is_absolute() and entry.is_file()
    src = entry.read_text(encoding="utf-8")
    assert "treeaicoach.main" in src and "main()" in src
    assert ROOT in [Path(p).resolve() for p in a.kwargs["pathex"]]
    datas = [(Path(s), d.replace("\\", "/")) for s, d in a.kwargs["datas"]]
    assert ((ROOT / "treeaicoach" / "assets").resolve(), "treeaicoach/assets") in [(s.resolve(), d) for s, d in datas]
    assert (ROOT / "treeaicoach" / "assets").is_dir()
    for src_path, _ in datas:
        if "/site/" not in src_path.as_posix():
            assert src_path.is_absolute() and src_path.exists(), src_path
    assert any(d.startswith("customtkinter") for _, d in datas), "customtkinter data files missing"


def test_spec_hidden_imports_and_excludes(monkeypatch):
    calls = _exec_spec(monkeypatch)
    kw = calls["analysis"].kwargs
    hidden = set(kw["hiddenimports"])
    assert REQUIRED_HIDDEN <= hidden
    assert _package_module_names() <= hidden, "every treeaicoach module must be bundled"
    assert "treeaicoach.__main__" not in hidden
    excludes = set(kw["excludes"])
    assert REQUIRED_EXCLUDES <= excludes
    assert not (RUNTIME_DEPS & excludes), "a runtime dependency is excluded"


def test_spec_filters_unused_binaries(monkeypatch):
    calls = _exec_spec(monkeypatch)
    a: _FakeAnalysis = calls["analysis"]
    names = [b[0] for b in a.binaries] + [d[0] for d in a.datas]
    assert not any("opencv_videoio_ffmpeg" in n for n in names)
    assert not any(n.endswith(".DS_Store") for n in names)
    assert any("onnxruntime" in n for n in names) and any("manifest.json" in n for n in names)


def test_spec_with_real_pyinstaller_helpers(monkeypatch):
    pytest.importorskip("PyInstaller.utils.hooks")
    pytest.importorskip("customtkinter")
    calls = _exec_spec(monkeypatch, fake_hooks=False)
    kw = calls["analysis"].kwargs
    ctk = [d for _, d in kw["datas"] if d.replace("\\", "/").startswith("customtkinter")]
    assert ctk, "collect_data_files('customtkinter') returned nothing"
    assert _package_module_names() <= set(kw["hiddenimports"])


def test_launcher_imports_main_lazily():
    tree = ast.parse((PACKAGING / "launcher.py").read_text(encoding="utf-8"))
    top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any(isinstance(n, ast.ImportFrom) and (n.module or "").startswith("treeaicoach") for n in top_level)
    lazy = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "treeaicoach.main"]
    assert lazy and lazy[0].names[0].name == "main"


# --------------------------------------------------------------------------- version
def test_versions_are_consistent():
    version = treeaicoach.__version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert _pyproject_version() == version, "pyproject.toml and treeaicoach.__version__ differ"

    info = _eval_version_file()
    ffi = _walk(info, "FixedFileInfo")[0][1]
    expected = _version_tuple(version)
    assert tuple(ffi["filevers"]) == expected and tuple(ffi["prodvers"]) == expected
    strings = {a[0]: a[1] for a, _ in _walk(info, "StringStruct")}
    dotted = ".".join(str(p) for p in expected)
    assert strings["FileVersion"] == dotted and strings["ProductVersion"] == dotted


def test_version_resource_strings():
    info = _eval_version_file()
    strings = {a[0]: a[1] for a, _ in _walk(info, "StringStruct")}
    assert strings["ProductName"] == "TreeAI Coach"
    assert strings["CompanyName"] == "TreeAI Coach"
    assert strings["FileDescription"] == "TreeAI Coach — coach vocal anti-gank pour League of Legends"
    assert strings["OriginalFilename"] == "TreeAICoach.exe"
    assert "TreeAI Coach" in strings["LegalCopyright"]
    tables = _walk(info, "StringTable")
    translations = _walk(info, "VarStruct")
    lang, codepage = int(tables[0][0][0][:4], 16), int(tables[0][0][0][4:], 16)
    assert translations[0][0][1] == [lang, codepage]


def test_version_resource_with_pyinstaller():
    if sys.platform != "win32":
        pytest.skip("PyInstaller's version-resource module is Windows-only")
    vi = pytest.importorskip("PyInstaller.utils.win32.versioninfo")
    info = vi.load_version_info_from_text_file(str(VERSION_FILE))
    assert len(info.toRaw()) > 500


# --------------------------------------------------------------------------- icon
def test_icon_png():
    from PIL import Image

    with Image.open(ICON_PNG) as im:
        assert im.size == (512, 512)
        rgba = im.convert("RGBA")
    assert rgba.getpixel((0, 0))[3] == 0 and rgba.getpixel((511, 511))[3] == 0, "corners must be transparent"
    assert rgba.getpixel((256, 256))[3] == 255
    colors = rgba.convert("RGB").getcolors(512 * 512) or []
    gold = sum(n for n, (r, g, b) in colors if abs(r - 200) < 40 and abs(g - 170) < 40 and abs(b - 110) < 45)
    navy = sum(n for n, (r, g, b) in colors if r < 40 and g < 60 and 15 < b < 100)
    assert gold > 5000 and navy > 50000


def test_icon_ico_has_all_sizes():
    from PIL import Image

    entries = _ico_entries(ICON_ICO.read_bytes())
    assert sorted(e["w"] for e in entries) == list(ICO_SIZES)
    assert all(e["w"] == e["h"] and e["bpp"] == 32 for e in entries)
    with Image.open(ICON_ICO) as im:
        assert set(im.info["sizes"]) == {(s, s) for s in ICO_SIZES}
        for s in (16, 32, 256):
            im.size = (s, s)
            px = im.convert("RGBA")
            assert px.getpixel((0, 0))[3] == 0 and px.getpixel((s // 2, s // 2))[3] == 255


def test_make_icon_renders_and_writes(tmp_path):
    mi = _load_make_icon()
    try:
        for size in (16, 48):
            im = mi.render_icon(size)
            assert im.mode == "RGBA" and im.size == (size, size)
            assert im.getpixel((0, 0))[3] == 0 and im.getpixel((size // 2, size // 2))[3] == 255
        small = [mi.render_icon(s, supersample=4) for s in (16, 24, 32)]
        out = tmp_path / "t.ico"
        mi.write_ico(small, out)
        assert [e["w"] for e in _ico_entries(out.read_bytes())] == [16, 24, 32]
        from PIL import Image

        with Image.open(out) as im:
            im.size = (32, 32)
            assert im.convert("RGBA").getpixel((16, 16))[3] == 255
    finally:
        sys.modules.pop("treeaicoach_make_icon", None)


# --------------------------------------------------------------------------- build script
def test_build_bat():
    raw = BAT.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n"), "build_exe.bat must use CRLF line endings"
    head = raw.split(b"chcp 65001", 1)[0]
    assert head.isascii(), "no non-ASCII character before 'chcp 65001'"
    text = raw.decode("utf-8")
    for needle in ("-m venv", "requirements-dev.txt", "-m pytest", "-m PyInstaller packaging\\treeaicoach.spec",
                   "--noconfirm", "dist\\TreeAICoach.exe", "pause", "--selftest"):
        assert needle in text, needle
    assert "é" in text, "messages are in French"


def test_requirements_dev():
    lines = [ln.split("#")[0].strip().lower() for ln in (ROOT / "requirements-dev.txt").read_text().splitlines()]
    lines = [ln for ln in lines if ln]
    assert "-r requirements.txt" in lines
    assert any(ln.startswith("pytest") for ln in lines)
    assert any(ln.startswith("pyinstaller") for ln in lines)
    runtime = (ROOT / "requirements.txt").read_text().lower()
    for pkg in ("onnx", "torch"):
        assert not any(re.match(rf"{pkg}\b", ln) for ln in lines), pkg   # onnxruntime is fine
        assert not re.search(rf"^{pkg}\b", runtime, re.M), pkg


# --------------------------------------------------------------------------- workflow
def _yaml_key_lines(text: str) -> list[tuple[int, str]]:
    """(indent, line) of the mapping/sequence lines, skipping block-scalar content (``run: |``...)."""
    out: list[tuple[int, str]] = []
    scalar_indent: int | None = None
    for ln in text.splitlines():
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(ln) - len(ln.lstrip(" "))
        if scalar_indent is not None:
            if indent > scalar_indent:
                continue
            scalar_indent = None
        out.append((indent, stripped))
        if re.search(r":\s*[|>][-+]?\s*$", stripped):
            scalar_indent = indent
    return out


def test_workflow_text():
    """Tiny YAML sanity check that works without PyYAML (Windows CI only has requirements-dev.txt)."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "\t" not in text
    assert text.count("${{") == text.count("}}")
    lines = _yaml_key_lines(text)
    assert lines[0] == (0, "name: Build TreeAICoach.exe")
    assert {s.split(":")[0] for i, s in lines if i == 0} == {"name", "on", "permissions", "jobs"}
    for indent, s in lines:
        assert indent % 2 == 0, f"odd indentation: {s!r}"
        assert re.match(r"(- )?[\w.-]+:( |$)|- ", s) or s.startswith(("'", '"', "- ")), f"not YAML-like: {s!r}"
    for needle in (
        "name: Build TreeAICoach.exe", "workflow_dispatch", '"v*"', "windows-latest",
        "actions/checkout@v4", "actions/setup-python@v5", "actions/upload-artifact@v4",
        'python-version: "3.11"', "cache: pip", "pip install -r requirements-dev.txt",
        "python -m pytest -q", "pyinstaller packaging/treeaicoach.spec --noconfirm --clean",
        "'--selftest', '--selftest-out'", "-Wait -PassThru", "'--ui-smoke'", "selftest.txt",
        "name: TreeAICoach-windows", "contents: write", "GH_TOKEN: ${{ github.token }}",
        "gh release delete latest --cleanup-tag -y || true", "gh release create latest",
        "--prerelease", "--target \"$GITHUB_SHA\"", "TreeAI Coach (dernière version)",
        "refs/tags/v", "startsWith(github.ref_name, 'claude')", "github.event.repository.default_branch",
    ):
        assert needle in text, needle


def test_workflow_structure():
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    on = data.get("on", data.get(True))  # YAML 1.1 reads the bare key `on` as True
    assert set(on) >= {"push", "workflow_dispatch"}
    assert on["push"]["tags"] == ["v*"] and on["push"]["branches"] == ["**"]
    assert data["permissions"]["contents"] == "write"
    build = data["jobs"]["build"]
    assert build["runs-on"] == "windows-latest"
    uses = [s["uses"] for job in data["jobs"].values() for s in job["steps"] if "uses" in s]
    assert all(re.fullmatch(r"[\w.-]+/[\w.-]+@v\d+", u) for u in uses), uses
    runs = "\n".join(s.get("run", "") for s in build["steps"])
    assert runs.index("pytest") < runs.index("pyinstaller") < runs.index("--selftest")


# --------------------------------------------------------------------------- README / LICENSE
def test_readme():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert LEGAL_NOTICE in text
    for needle in ("Télécharger", "Démarrage en 3 étapes", "Sans bordure", "Informations complémentaires",
                   "Exécuter quand même", "Sécurité et règles de Riot", "Dépannage", "Pour les développeurs",
                   "Heure et langue", "F9", "F10", "F11", "training/README.md", "TreeAICoach.exe",
                   "python -m treeaicoach", "pytest", "mars 2025"):
        assert needle in text, needle
    for link in ("packaging/icon.png", "LICENSE", "docs/ARCHITECTURE.md", "docs/MINIMAP_FACTS.md"):
        assert link in text and (ROOT / link).exists(), link


def test_license():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License")
    assert "TreeAI Coach contributors" in text
    assert "THE SOFTWARE IS PROVIDED \"AS IS\"" in text
