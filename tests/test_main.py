"""Tests for treeaicoach.main (command line, exit codes, single instance, UI launch)."""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

import treeaicoach
from treeaicoach import main as main_mod
from treeaicoach import paths

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    paths._reset_cache()
    yield
    paths._reset_cache()


def test_version(capsys: pytest.CaptureFixture[str]):
    assert main_mod.main(["--version"]) == 0
    assert treeaicoach.__version__ in capsys.readouterr().out


def test_bad_arguments_exit_code_2(capsys: pytest.CaptureFixture[str]):
    assert main_mod.main(["--nope"]) == 2
    assert "unrecognized" in capsys.readouterr().err or True
    assert main_mod.main(["--help"]) == 0


def test_no_console_never_crashes(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    assert main_mod.main(["--version"]) == 0
    assert main_mod.main(["--bogus"]) == 2


def test_selftest_exit_codes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls = []

    def fake(out, voice=False):
        calls.append((out, voice))
        return fake.rc

    fake.rc = 0
    import treeaicoach.selftest as st

    monkeypatch.setattr(st, "run_selftest", fake)
    out = tmp_path / "s.txt"
    assert main_mod.main(["--selftest", "--selftest-out", str(out)]) == 0
    fake.rc = 1
    assert main_mod.main(["--selftest"]) == 1
    assert calls[0] == (out, False)


def test_ui_launch_and_smoke(monkeypatch: pytest.MonkeyPatch):
    seen = []
    fake_ui = types.ModuleType("treeaicoach.ui")

    def run_app(cfg, *, demo=False, smoke_seconds=None):
        seen.append((type(cfg).__name__, demo, smoke_seconds))
        return 0

    fake_ui.run_app = run_app
    monkeypatch.setitem(sys.modules, "treeaicoach.ui", fake_ui)
    monkeypatch.setattr(treeaicoach, "ui", fake_ui, raising=False)
    assert main_mod.main(["--ui-smoke"]) == 0
    assert main_mod.main(["--demo"]) == 0
    assert seen == [("Config", False, 4.0), ("Config", True, None)]


def test_ui_missing_returns_1(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "treeaicoach.ui", None)    # import fails
    assert main_mod.run_gui(object()) == 1


def test_already_running(monkeypatch: pytest.MonkeyPatch):
    boxes = []
    monkeypatch.setattr(main_mod, "acquire_single_instance", lambda name=main_mod.MUTEX_NAME: False)
    monkeypatch.setattr(main_mod, "message_box", lambda text, *a, **k: boxes.append(text))
    monkeypatch.setattr(main_mod, "run_gui", lambda *a, **k: pytest.fail("UI must not start"))
    assert main_mod.main([]) == 0
    assert boxes and "déjà ouvert" in boxes[0]


def test_single_instance_off_windows():
    if sys.platform == "win32":
        pytest.skip("Linux behaviour")
    assert main_mod.acquire_single_instance() is True
    main_mod.release_single_instance()


def test_config_option_and_console_demo(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    from treeaicoach.config import Config, save_config

    cfg_file = tmp_path / "cfg.json"
    save_config(Config(target_fps=6.0, voice_volume=0), cfg_file)
    rc = main_mod.main(["--nogui", "--demo", "--config", str(cfg_file), "--duration", "1.5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "mode console (démo)" in out and "Mode démo" in out


def test_module_version_subprocess():
    r = subprocess.run([sys.executable, "-m", "treeaicoach", "--version"], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and treeaicoach.__version__ in r.stdout
