# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec of the TreeAI Coach app (one-DIR, windowed). Requires PyInstaller >= 6.

Run from the repository root:

    pyinstaller packaging/treeaicoach.spec --noconfirm --clean

Output: dist/TreeAICoach-app/TreeAICoach-app.exe (+ _internal/). The exe users download
(release/TreeAICoach.exe) is the small launcher of packaging/bootstrap.spec: it downloads this folder
(zipped in chunks by packaging/make_release.py) once to %LOCALAPPDATA%\\TreeAICoach\\app\\<version>
and starts it from there (the former one-file exe re-extracted ~250 MB to %TEMP% at every launch).
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
APP_NAME = "TreeAICoach-app"  # one-dir folder + exe; release/TreeAICoach.exe is the launcher

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
    "PySide6.QtCore",       # launcher (Qt Widgets, docs/LAUNCHER.md); PyInstaller's own PySide6 hook adds
    "PySide6.QtGui",        # the platform plugin (qwindows.dll) and the Qt DLLs
    "PySide6.QtWidgets",
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
# Bot Discord (discord_bot.py): discord.py (client + voice), PyNaCl, davey (DAVE E2EE, Rust .pyd) and the
# libopus DLL discord.py ships in discord/bin (loaded from next to discord/opus.py)
hiddenimports += [m for m in _optional_submodules("discord") if not m.startswith(("discord.ext", "discord.app_commands"))]
hiddenimports += _optional_submodules("nacl") + ["davey", "davey.davey"]
try:
    datas += [d for d in collect_data_files("discord") if os.path.basename(d[0]).lower().startswith("libopus")
              and "x64" in os.path.basename(d[0]).lower()]
    binaries += collect_dynamic_libs("davey") + collect_dynamic_libs("nacl")
except Exception:
    pass

# Voice assistant (voice_assistant.py): vosk (cffi binding + libvosk.dll and its MinGW runtime DLLs, loaded
# from the package folder) and sounddevice (+ the PortAudio DLL in _sounddevice_data)
# vosk itself (libvosk.dll ~17 MB compressed) is downloaded on first use (voice_assistant.import_vosk: GitHub's
# 100 MB file limit); its pure-Python dependencies stay bundled
hiddenimports += ["sounddevice", "_cffi_backend", "srt", "requests", "tqdm"]
# local Whisper (stt_local.py): the CTranslate2 + tokenizers wheels are downloaded on first use (pinned
# SHA-256, ~21 MB) and imported from the cache: only the stdlib modules they import are bundled here
hiddenimports += ["asyncio", "argparse", "concurrent.futures", "subprocess", "signal", "socket", "ssl", "queue",
                  "gettext", "locale", "bz2", "lzma", "heapq", "array", "copy", "string", "traceback", "atexit",
                  "glob", "ctypes", "importlib.util"]
# ctranslate2 4.x imports pkg_resources on Windows (setuptools, not bundled): stt_local._shim_pkg_resources
excludes_extra = ["vosk", "ctranslate2", "tokenizers", "faster_whisper"]
try:
    datas += collect_data_files("_sounddevice_data")
except Exception:
    pass

excludes = excludes_extra + [
    # unused heavy modules (exe < 100 MB: GitHub file limit): Pillow AVIF codec, MFC bindings
    "PIL._avif", "PIL.AvifImagePlugin", "win32ui", "pythonwin", "win32uiole", "dde",
    # training / development only
    "torch", "torchvision", "torchaudio", "onnx", "onnxscript", "tensorboard",
    "matplotlib", "scipy", "pandas", "sympy",
    "pytest", "_pytest", "IPython", "jupyter", "notebook",
    "training",
    # other GUI toolkits (the launcher is Qt Widgets: QtCore / QtGui / QtWidgets only)
    "PyQt5", "PyQt6", "PySide2", "tkinter", "_tkinter", "customtkinter",
    "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore", "PySide6.QtPdf",
    "PySide6.QtOpenGL", "PySide6.QtSvg", "PySide6.QtMultimedia", "PySide6.Qt3DCore",
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


#: Qt files the launcher never loads (software OpenGL 20 MB, translations, unused plugins / modules).
_QT_DROP = ("opengl32sw.dll", "qt6pdf", "qt6quick", "qt6qml", "qt6network", "qt6opengl", "qt6svg",
            "qt6virtualkeyboard", "qdirect2d", "qminimal", "qoffscreen", "qwebgl", "qtvirtualkeyboard",
            "qpdf", "qsvg", "qtuiotouch", "qnetworklistmanager", "qtiff", "qwebp", "qicns", "qtga", "qwbmp",
            "qjpeg", "qgif")


def _keep(entry):
    """Drop files the app never uses: OpenCV's FFmpeg video plugin (~25 MB, Windows), unused Qt parts,
    Qt translations and macOS litter."""
    base = os.path.basename(entry[0]).lower()
    path = entry[0].replace("\\", "/").lower()
    if "pyside6/translations/" in path or (base.startswith(_QT_DROP) and "pyside6" in path):
        return False
    return not (base.startswith("opencv_videoio_ffmpeg") or base == ".ds_store")


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # one-dir: binaries / data go next to the exe (COLLECT)
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,         # UPX-packed exes trigger antivirus false positives
    upx_exclude=[],
    console=False,     # windowed app (sys.stdout is None)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_ICO,
    version=VERSION_FILE,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
