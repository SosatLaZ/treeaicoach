# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec of release/TreeAICoach.exe: the small launcher / updater (one-file, windowed, stdlib only).

    pyinstaller packaging/bootstrap.spec --noconfirm --clean     ->  dist/TreeAICoach.exe

It installs and starts the one-dir app (packaging/treeaicoach.spec -> dist/TreeAICoach-app/), published
in chunks by packaging/make_release.py.
"""

import os

PACKAGING = os.path.abspath(SPECPATH)  # noqa: F821
excludes = [
    "_bz2", "bz2", "_lzma", "lzma", "_decimal", "decimal", "_sqlite3", "sqlite3",
    "_elementtree", "xml.etree", "unittest", "pydoc", "tkinter", "_tkinter",
    "asyncio", "_asyncio", "multiprocessing", "_multiprocessing", "concurrent",
    "_overlapped", "numpy", "PySide6", "cv2", "onnxruntime", "treeaicoach",
]
a = Analysis(  # noqa: F821
    [os.path.join(PACKAGING, "bootstrap.py")],
    pathex=[], binaries=[], datas=[], hiddenimports=[], hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=excludes, noarchive=False, optimize=1,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz, a.scripts, a.binaries, a.datas, [],
    name="TreeAICoach",
    debug=False, bootloader_ignore_signals=False, strip=False,
    upx=False,          # UPX-packed exes trigger antivirus false positives
    runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
    icon=os.path.join(PACKAGING, "icon.ico"),
    version=os.path.join(PACKAGING, "version_info.txt"),
)
