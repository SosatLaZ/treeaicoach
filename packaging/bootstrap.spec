# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec of the TreeAICoach.exe bootstrapper stub (one-file, windowed, stdlib only).

    pyinstaller packaging/bootstrap.spec --noconfirm --clean     ->  dist/TreeAICoachBoot.exe

packaging/make_bundle.py then appends the zipped one-dir app (packaging/treeaicoach.spec) to it.
"""

import os

PACKAGING = os.path.abspath(SPECPATH)  # noqa: F821
excludes = [
    "_hashlib", "_ssl", "ssl", "_bz2", "bz2", "_lzma", "lzma", "_decimal", "decimal", "_sqlite3", "sqlite3",
    "_elementtree", "pyexpat", "xml", "email", "http", "unittest", "pydoc", "tkinter", "_tkinter",
    "asyncio", "_asyncio", "multiprocessing", "_multiprocessing", "concurrent", "select", "_socket", "socket",
    "_overlapped", "_queue", "unicodedata", "numpy", "PySide6", "cv2", "onnxruntime", "treeaicoach",
]
a = Analysis(  # noqa: F821
    [os.path.join(PACKAGING, "bootstrap.py")],
    pathex=[], binaries=[], datas=[], hiddenimports=[], hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=excludes, noarchive=False, optimize=1,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz, a.scripts, a.binaries, a.datas, [],
    name="TreeAICoachBoot",
    debug=False, bootloader_ignore_signals=False, strip=False,
    upx=False,          # UPX-packed exes trigger antivirus false positives
    runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
    icon=os.path.join(PACKAGING, "icon.ico"),
    version=os.path.join(PACKAGING, "version_info.txt"),
)
