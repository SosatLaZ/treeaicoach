# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec of TreeAICoach.exe (one-file, windowed). Requires PyInstaller >= 6.

Run from the repository root:

    pyinstaller packaging/treeaicoach.spec --noconfirm --clean

Output: dist/TreeAICoach.exe. Every path below is absolute (built from SPECPATH, the folder of
this file), so the build does not depend on the current directory.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))  # noqa: F821 (SPECPATH: set by PyInstaller)
PACKAGING = os.path.join(ROOT, "packaging")
PKG_DIR = os.path.join(ROOT, "treeaicoach")
ASSETS_DIR = os.path.join(PKG_DIR, "assets")
ENTRY_SCRIPT = os.path.join(PACKAGING, "launcher.py")       # imports treeaicoach.main.main
ICON_ICO = os.path.join(PACKAGING, "icon.ico")
ICON_PNG = os.path.join(PACKAGING, "icon.png")
VERSION_FILE = os.path.join(PACKAGING, "version_info.txt")
APP_NAME = "TreeAICoach"

for _required in (PKG_DIR, ASSETS_DIR, ENTRY_SCRIPT, ICON_ICO, VERSION_FILE):
    if not os.path.exists(_required):
        raise SystemExit(f"[treeaicoach.spec] fichier ou dossier introuvable : {_required}")
if not os.path.isfile(os.path.join(ASSETS_DIR, "model", "minimap_detector.onnx")):
    print("[treeaicoach.spec] ATTENTION : assets/model/minimap_detector.onnx absent - "
          "l'exe utilisera le détecteur classique de secours.")

# The treeaicoach package must be importable while the spec runs (collect_submodules).
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _package_modules():
    """All treeaicoach modules (PyInstaller helper + plain file listing as a safety net)."""
    names = set(collect_submodules("treeaicoach"))
    for fname in os.listdir(PKG_DIR):
        stem, ext = os.path.splitext(fname)
        if ext == ".py" and stem != "__main__":
            names.add("treeaicoach" if stem == "__init__" else f"treeaicoach.{stem}")
    names.discard("treeaicoach.__main__")
    return sorted(names)


datas = [
    (ASSETS_DIR, "treeaicoach/assets"),             # everything: textures, icons, ONNX model, selftest...
    (ICON_ICO, "packaging"),                        # app icon, same relative place as in a checkout:
    (ICON_PNG, "packaging"),                        #   paths.package_dir().parent / "packaging" / "icon.*"
]
datas += collect_data_files("customtkinter")        # themes (.json) and fonts
_NOTICES = os.path.join(ROOT, "THIRD_PARTY_NOTICES.md")   # MIT notices of adapted code (lcu.py)
if os.path.isfile(_NOTICES):
    datas.append((_NOTICES, "."))

binaries = collect_dynamic_libs("onnxruntime")      # onnxruntime*.dll (the contrib hook does it too)
# Bundle the Microsoft C++ runtime (msvcp140*.dll, concrt140.dll) when it sits next to python.exe
# (pip install msvc-runtime) so the exe also runs on PCs without the VC++ redistributable.
for _dll in ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "concrt140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
    _p = os.path.join(sys.base_prefix, _dll)
    if os.path.isfile(_p):
        binaries.append((_p, "."))

hiddenimports = [
    "win32com",
    "win32com.client",
    "pythoncom",
    "pywintypes",
    "PIL._tkinter_finder",
    "customtkinter",
    "onnxruntime",
    "mss",
    # natural voice (tts_neural): edge-tts + aiohttp, miniaudio (cffi), WinRT OneCore voices
    "edge_tts",
    "aiohttp",
    "certifi",
    "miniaudio",
    "_miniaudio",
    "_cffi_backend",
] + _package_modules()


def _optional_submodules(pkg):
    try:
        return collect_submodules(pkg)
    except Exception:
        return []


hiddenimports += _optional_submodules("edge_tts")
hiddenimports += [m for m in _optional_submodules("winrt")
                  if m.startswith(("winrt.system", "winrt._winrt", "winrt.windows.foundation",
                                   "winrt.windows.media.speechsynthesis", "winrt.windows.storage.streams"))
                  or m == "winrt"]
try:
    datas += collect_data_files("certifi")          # cacert.pem (edge-tts TLS)
except Exception:
    pass

excludes = [
    # training / development only
    "torch", "torchvision", "torchaudio", "onnx", "onnxscript", "tensorboard",
    "matplotlib", "scipy", "pandas", "sympy",
    "pytest", "_pytest", "IPython", "jupyter", "notebook",
    "training",
    # other GUI toolkits that could be dragged in by optional imports
    "PyQt5", "PyQt6", "PySide2", "PySide6",
]

a = Analysis(  # noqa: F821
    [ENTRY_SCRIPT],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)


def _keep(entry):
    """Drop files the app never uses: OpenCV's FFmpeg video plugin (~25 MB, Windows) and macOS litter."""
    base = os.path.basename(entry[0]).lower()
    return not (base.startswith("opencv_videoio_ffmpeg") or base == ".ds_store")


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,        # one-file: binaries and data go inside the exe (no COLLECT step)
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,         # UPX-packed exes trigger antivirus false positives
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,     # windowed app (sys.stdout is None)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_ICO,
    version=VERSION_FILE,
)
