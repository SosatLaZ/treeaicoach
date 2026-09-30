"""Tests for treeaicoach.paths (resource and user-data locations)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

from treeaicoach import paths

REPO_PKG = Path(__file__).resolve().parents[1] / "treeaicoach"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Fresh path cache, user dir in tmp, temp fallback redirected into tmp."""
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "systemp"))
    (tmp_path / "systemp").mkdir()
    paths._reset_cache()
    yield
    paths._reset_cache()


def test_package_dir_from_source():
    assert paths.package_dir() == REPO_PKG
    assert (paths.package_dir() / "__init__.py").is_file()


def test_asset_path_joins_parts():
    p = paths.asset_path("icons", "champions", "index.json")
    assert p == REPO_PKG / "assets" / "icons" / "champions" / "index.json"
    assert paths.asset_path() == REPO_PKG / "assets"
    assert paths.asset_path(Path("minimap")) == REPO_PKG / "assets" / "minimap"


def test_package_dir_pyinstaller_onefile(monkeypatch, tmp_path):
    meipass = tmp_path / "_MEI12345"
    (meipass / "treeaicoach" / "assets").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    assert paths.is_frozen()
    assert paths.package_dir() == meipass / "treeaicoach"
    assert paths.asset_path("model", "x.onnx") == meipass / "treeaicoach" / "assets" / "model" / "x.onnx"


def test_package_dir_pyinstaller_onedir(monkeypatch, tmp_path):
    exe_dir = tmp_path / "dist" / "TreeAICoach"
    (exe_dir / "_internal" / "treeaicoach" / "assets").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "TreeAICoach.exe"))
    assert paths.package_dir() == exe_dir / "_internal" / "treeaicoach"


def test_package_dir_frozen_without_assets_still_returns_a_path(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "nothing"), raising=False)
    assert paths.package_dir() == tmp_path / "nothing" / "treeaicoach"


def test_env_override_and_lazy_creation(tmp_path):
    home = tmp_path / "home"
    assert not home.exists()  # nothing created at import / before first use
    assert paths.user_data_dir() == home
    assert home.is_dir()
    assert paths.logs_dir() == home / "logs" and (home / "logs").is_dir()
    assert paths.cache_dir() == home / "cache" and (home / "cache").is_dir()
    assert paths.collect_dir() == home / "collect" and (home / "collect").is_dir()
    assert paths.config_path() == home / "config.json"
    assert not paths.config_path().exists()
    # no probe files left behind
    assert [p.name for p in home.iterdir() if p.is_file()] == []


def test_env_override_expands_user_and_relative(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(paths.ENV_HOME, "rel_home")
    assert paths.user_data_dir() == tmp_path / "rel_home"


def test_directory_recreated_if_deleted(tmp_path):
    logs = paths.logs_dir()
    logs.rmdir()
    assert paths.logs_dir() == logs
    assert logs.is_dir()


def test_windows_appdata(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.ENV_HOME, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert paths.user_data_dir() == tmp_path / "Roaming" / "TreeAICoach"


def test_windows_without_appdata_uses_home(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.ENV_HOME, raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path / "userprofile"))
    assert paths.user_data_dir() == tmp_path / "userprofile" / "AppData" / "Roaming" / "TreeAICoach"


def test_unix_default_in_home(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.ENV_HOME, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path / "unixhome"))
    assert paths.user_data_dir() == tmp_path / "unixhome" / ".treeaicoach"


def test_home_unknown_does_not_raise(monkeypatch, tmp_path):
    def boom(cls):
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.delenv(paths.ENV_HOME, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(paths.Path, "home", classmethod(boom))
    d = paths.user_data_dir()
    assert d.is_dir()
    assert str(d).startswith(str(tmp_path / "systemp"))


def test_unwritable_location_falls_back_to_temp(monkeypatch, tmp_path):
    blocker = tmp_path / "a_file"
    blocker.write_text("not a directory")
    monkeypatch.setenv(paths.ENV_HOME, str(blocker / "sub"))  # cannot mkdir below a file
    d = paths.user_data_dir()
    assert d == tmp_path / "systemp" / "TreeAICoach"
    assert d.is_dir()
    # sub-directories follow the fallback and are usable
    logs = paths.logs_dir()
    assert logs.is_dir() and logs.parent == d
    (logs / "x.txt").write_text("ok")
    # cached: same answer, no exception
    assert paths.user_data_dir() == d


def test_second_fallback_when_temp_is_unusable(monkeypatch, tmp_path):
    blocker = tmp_path / "a_file"
    blocker.write_text("x")
    monkeypatch.setenv(paths.ENV_HOME, str(blocker / "sub"))
    # make the stable temp fallback unusable too: a file where the folder should be
    (tmp_path / "systemp" / "TreeAICoach").write_text("x")
    d = paths.user_data_dir()
    assert d.is_dir()
    assert d.name.startswith("TreeAICoach-")


def test_never_raises_with_invalid_path(monkeypatch):
    # NUL is invalid in paths on all OSes (os.mkdir raises ValueError)
    monkeypatch.setattr(paths, "_candidate_user_dir", lambda: Path("bad\x00dir"))
    d = paths.user_data_dir()
    assert isinstance(d, Path)
    assert isinstance(paths.logs_dir(), Path)
    assert isinstance(paths.config_path(), Path)
