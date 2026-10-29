"""Local Whisper engine import in the frozen exe (no setuptools): ctranslate2 4.x's Windows ``__init__``
runs ``import pkg_resources`` — the user's PC failed there and never loaded Whisper."""

from __future__ import annotations

import sys
import textwrap

from treeaicoach import stt_local

#: the head of ctranslate2 4.4.0's real ``__init__.py`` (Windows branch), then a version
_CT2_INIT = textwrap.dedent('''
    import sys

    if sys.platform == "win32":
        import ctypes
        import glob
        import os

        import pkg_resources

        module_name = sys.modules[__name__].__name__
        package_dir = pkg_resources.resource_filename(module_name, "")

        add_dll_directory = getattr(os, "add_dll_directory", None)
        if add_dll_directory is not None:
            add_dll_directory(package_dir)

        for library in glob.glob(os.path.join(package_dir, "*.dll")):
            ctypes.CDLL(library)
        PACKAGE_DIR = package_dir

    __version__ = "4.4.0"
''')


def _fake_engine(tmp_path, monkeypatch):  # noqa: ANN001, ANN202
    (tmp_path / "ctranslate2").mkdir()
    (tmp_path / "ctranslate2" / "__init__.py").write_text(_CT2_INIT, encoding="utf-8")
    (tmp_path / "tokenizers").mkdir()
    (tmp_path / "tokenizers" / "__init__.py").write_text("__version__ = '0.20.3'\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    for m in ("ctranslate2", "tokenizers"):
        monkeypatch.delitem(sys.modules, m, raising=False)
    monkeypatch.setitem(sys.modules, "pkg_resources", None)     # the frozen exe: no setuptools
    monkeypatch.setattr(sys, "platform", "win32")


def test_engine_imports_without_pkg_resources(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    _fake_engine(tmp_path, monkeypatch)
    eng = stt_local.import_engine(tmp_path / "cache")
    assert eng is not None, stt_local.ENGINE_ERROR
    ct2, _tok = eng
    assert ct2.__version__ == "4.4.0" and stt_local.ENGINE_ERROR == ""
    assert ct2.PACKAGE_DIR.rstrip("/\\").endswith("ctranslate2")
    assert getattr(sys.modules["pkg_resources"], "__treeai_shim__", False)


def test_diagnose_reports_engine_loaded(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    _fake_engine(tmp_path, monkeypatch)
    d = stt_local.diagnose(tmp_path / "cache")
    assert d["engine"] is True and d["version"] == "4.4.0" and d["error"] == "" and d["model"] is False


def test_engine_error_is_shown_with_its_reason(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    (tmp_path / "ctranslate2").mkdir()
    (tmp_path / "ctranslate2" / "__init__.py").write_text("import some_missing_dep_xyz\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "ctranslate2", raising=False)
    w = stt_local.WhisperSTT(root=tmp_path / "cache")
    assert not w.load()
    assert "some_missing_dep_xyz" in w.status and "Vosk" in w.status
    d = stt_local.diagnose(tmp_path / "cache")
    assert d["engine"] is False and "some_missing_dep_xyz" in d["error"]


def test_ct2_native_threads_lowered_once_python_threads_kept():
    """3.4.1: CTranslate2 computes on its own native threads: the ones that appear during a Whisper
    call are lowered to below normal (once each); Python's own threads are left alone."""
    import threading as _th

    from treeaicoach import stt_local as sl

    sl._DEMOTED.clear()
    me = _th.current_thread().native_id
    lowered = []
    n = sl.demote_new_threads({1, 2}, snapshot=lambda: {1, 2, 50, 51, me}, setter=lambda t: lowered.append(t) or True)
    assert n == 2 and sorted(lowered) == [50, 51]
    assert sl.demote_new_threads({1, 2}, snapshot=lambda: {1, 2, 50, 51}, setter=lambda t: lowered.append(t) or True) == 0
    assert sl.CPU_THREADS <= 2
    if sys.platform != "win32":
        assert sl.native_threads() == set() and sl.demote_new_threads(set()) == 0
    sl._DEMOTED.clear()
