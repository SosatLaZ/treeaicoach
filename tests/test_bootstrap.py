"""Bootstrapper (packaging/bootstrap.py) + bundler (packaging/make_bundle.py): the release exe unpacks its
one-dir app once to %LOCALAPPDATA%\\TreeAICoach\\app\\<version>-<sha8> instead of at every launch."""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"tac_{name}", PACKAGING / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


boot = _load("bootstrap")
bundle = _load("make_bundle")


@pytest.fixture()
def built(tmp_path):
    app = tmp_path / "app"
    (app / "_internal" / "sub").mkdir(parents=True)
    (app / "TreeAICoach.exe").write_bytes(b"MZ fake app")
    (app / "_internal" / "sub" / "data.bin").write_bytes(os.urandom(5000))
    stub = tmp_path / "stub.exe"
    stub.write_bytes(b"MZ stub " + b"x" * 1000)
    out = tmp_path / "TreeAICoach.exe"
    meta = bundle.build(stub, app, "2.14.0", out)
    return app, out, meta


def test_bundle_layout_and_trailer(built):
    _app, out, meta = built
    assert boot.read_trailer(str(out)) == {k: v for k, v in meta.items() if k != "total"}
    assert meta["offset"] == 1008 and meta["files"] == 2 and meta["version"] == "2.14.0"
    assert out.read_bytes().endswith(boot.MAGIC)
    assert boot.payload_sha256(str(out), meta) == meta["sha256"]


def test_read_trailer_rejects_non_bundles(tmp_path):
    p = tmp_path / "x.exe"
    p.write_bytes(b"MZ" * 100)
    assert boot.read_trailer(str(p)) is None
    assert boot.read_trailer(str(tmp_path / "missing.exe")) is None
    p.write_bytes(b"garbage" + boot.make_trailer({"offset": 0, "size": 999999, "sha256": "0" * 64}))
    assert boot.read_trailer(str(p)) is None              # size beyond the file


def test_install_once_then_reuse(built, tmp_path):
    app, out, meta = built
    root = tmp_path / "apps"
    dest = boot.install(str(out), meta, str(root))
    assert Path(dest).name == f"2.14.0-{meta['sha256'][:8]}"
    assert (Path(dest) / "TreeAICoach.exe").read_bytes() == b"MZ fake app"
    assert (Path(dest) / "_internal/sub/data.bin").read_bytes() == (app / "_internal/sub/data.bin").read_bytes()
    assert boot.is_installed(dest, meta)
    stamp = (Path(dest) / "TreeAICoach.exe").stat().st_mtime_ns
    calls = []
    orig = boot.payload_sha256
    boot.payload_sha256 = lambda *a: calls.append(a) or orig(*a)
    try:
        assert boot.install(str(out), meta, str(root)) == dest      # second launch: no hash, no unpack
    finally:
        boot.payload_sha256 = orig
    assert calls == [] and (Path(dest) / "TreeAICoach.exe").stat().st_mtime_ns == stamp
    assert [p.name for p in root.iterdir()] == [Path(dest).name]   # no temp folder left


def test_install_repairs_damaged_copy(built, tmp_path):
    _app, out, meta = built
    dest = Path(boot.install(str(out), meta, str(tmp_path / "apps")))
    (dest / "TreeAICoach.exe").unlink()
    assert not boot.is_installed(str(dest), meta)
    assert boot.install(str(out), meta, str(tmp_path / "apps")) == str(dest)
    assert (dest / "TreeAICoach.exe").is_file()


def test_install_refuses_corrupted_exe(built, tmp_path):
    _app, out, meta = built
    data = bytearray(out.read_bytes())
    data[meta["offset"] + 40] ^= 0xFF
    out.write_bytes(bytes(data))
    with pytest.raises(boot.BootError, match="Télécharge"):
        boot.install(str(out), meta, str(tmp_path / "apps"))
    assert not any((tmp_path / "apps").iterdir())


def test_cleanup_old_versions(tmp_path):
    root = tmp_path / "apps"
    for n in ("2.13.1-aaaa", "2.14.0-bbbb", ".tmp-1-1", ".tmp-2-2"):
        (root / n).mkdir(parents=True)
    old = time.time() - 2 * boot.STALE_TMP_S
    os.utime(root / ".tmp-1-1", (old, old))
    removed = boot.cleanup_old(str(root), str(root / "2.14.0-bbbb"))
    assert sorted(removed) == [".tmp-1-1", "2.13.1-aaaa"]
    assert sorted(p.name for p in root.iterdir()) == [".tmp-2-2", "2.14.0-bbbb"]   # recent temp: maybe in use


def test_child_env_resets_pyinstaller_and_names_launcher():
    env = boot.child_env(r"C:\Users\x\Downloads\TreeAICoach.exe",
                         {"PATH": "p", "_MEIPASS2": "t", "_PYI_ARCHIVE_FILE": "a", "_PYI_PARENT_PROCESS_LEVEL": "1"})
    assert env["PATH"] == "p" and env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert not any(k.startswith(("_MEI", "_PYI")) for k in env)
    assert env["TREEAICOACH_LAUNCHER"].endswith("TreeAICoach.exe")
    assert float(env["TREEAICOACH_LAUNCH_T0"]) > 0


def test_main_runs_installed_app_with_args(built, tmp_path, monkeypatch):
    _app, out, meta = built
    monkeypatch.setattr(sys, "executable", str(out))
    monkeypatch.setenv("TREEAICOACH_APPS_DIR", str(tmp_path / "apps"))
    seen = {}

    class _P:
        def __init__(self, cmd, **kw):
            seen["cmd"], seen["env"] = cmd, kw["env"]

        def wait(self):
            return 7

    monkeypatch.setattr(boot.subprocess, "Popen", _P)
    assert boot.main(["--selftest", "--selftest-out", "x.txt"]) == 7
    assert seen["cmd"][0].endswith("TreeAICoach.exe") and seen["cmd"][1:] == ["--selftest", "--selftest-out", "x.txt"]
    assert Path(seen["cmd"][0]).parent.parent == tmp_path / "apps"
    assert seen["env"]["TREEAICOACH_LAUNCHER"] == str(out)


def test_main_without_payload_fails_cleanly(tmp_path, monkeypatch):
    p = tmp_path / "stub.exe"
    p.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "executable", str(p))
    monkeypatch.setattr(boot, "_message", lambda text: None)
    assert boot.main([]) == 2


def test_bundle_size_limit(tmp_path, built):
    app, _out, _meta = built
    with pytest.raises(SystemExit, match="limite"):
        bundle.build(tmp_path / "stub.exe", app, "1.0.0", tmp_path / "big.exe", max_bytes=100)


def test_launcher_exe_prefers_bootstrapper(tmp_path, monkeypatch):
    from treeaicoach import paths

    stub = tmp_path / "TreeAICoach.exe"
    stub.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv(paths.LAUNCHER_ENV, str(stub))
    assert paths.launcher_exe() == stub
    monkeypatch.setenv(paths.LAUNCHER_ENV, str(tmp_path / "gone.exe"))
    assert paths.launcher_exe() == Path(sys.executable)
    monkeypatch.delenv(paths.LAUNCHER_ENV)
    assert paths.launcher_exe() == Path(sys.executable)


def test_updater_replaces_the_bootstrapper(tmp_path, monkeypatch):
    from treeaicoach import paths, updater

    stub = tmp_path / "TreeAICoach.exe"
    stub.write_bytes(b"MZ old")
    new = tmp_path / "upd" / "TreeAICoach.exe"
    new.parent.mkdir()
    new.write_bytes(b"MZ new")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv(paths.LAUNCHER_ENV, str(stub))
    seen = {}
    res = updater.apply_update(new, popen=lambda cmd, **kw: seen.update(kw), pid=1234)
    assert res.ok, res.message
    assert seen["env"]["TAC_DST"] == str(stub.resolve())


def test_startup_marks():
    from treeaicoach import startup

    t = startup.mark("unit")
    assert t >= 0 and "unit=" in startup.summary()
