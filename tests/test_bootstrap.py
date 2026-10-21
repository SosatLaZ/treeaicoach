"""Launcher (packaging/bootstrap.py, release/TreeAICoach.exe) + release layout (packaging/make_release.py):
the one-dir app is published in chunks, downloaded once to %LOCALAPPDATA%\\TreeAICoach\\app\\<version>
and started from there; one-button updates through the launcher; rollback when a new version crashes."""

from __future__ import annotations

import functools
import http.server
import importlib.util
import json
import os
import shutil
import sys
import threading
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
rel = _load("make_release")


class _Quiet(http.server.SimpleHTTPRequestHandler):
    hits: list[str] = []

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        _Quiet.hits.append(self.path)
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if rng and os.path.isfile(path):
            start = int(rng.split("=")[1].rstrip("-"))
            data = Path(path).read_bytes()[start:]
            self.send_response(206)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()


@pytest.fixture()
def server(tmp_path):
    root = tmp_path / "www"
    (root / "release").mkdir(parents=True)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(root)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    _Quiet.hits = []
    yield root / "release", f"http://127.0.0.1:{httpd.server_address[1]}/release/version.json"
    httpd.shutdown()


def _app(tmp_path: Path, tag: str, n: int = 6) -> Path:
    app = tmp_path / f"app-{tag}"
    shutil.rmtree(app, ignore_errors=True)
    (app / "_internal" / "sub").mkdir(parents=True)
    (app / "TreeAICoach-app.exe").write_bytes(b"MZ app " + tag.encode())
    for i in range(n):
        (app / "_internal" / "sub" / f"f{i}.bin").write_bytes(os.urandom(3000))
    return app


def _publish(tmp_path, release: Path, version: str, limit: int = 8000) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    launcher = tmp_path / "launcher.exe"
    launcher.write_bytes(b"MZ launcher")
    return rel.build(launcher, _app(tmp_path, version), release, version, "notes", limit=limit)


@pytest.fixture()
def env(tmp_path, monkeypatch, server):
    release, url = server
    monkeypatch.setenv("TREEAICOACH_CHANNEL_URL", url)
    monkeypatch.setenv("TREEAICOACH_APPS_DIR", str(tmp_path / "apps"))
    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TREEAICOACH_NO_WINDOW", "1")
    monkeypatch.setattr(boot, "message", lambda *a, **k: None)
    me = tmp_path / "dl" / "TreeAICoach.exe"
    me.parent.mkdir()
    me.write_bytes(b"MZ launcher")
    monkeypatch.setattr(sys, "executable", str(me))
    runs: list[tuple[list[str], dict]] = []
    codes: list[int] = []

    class _P:
        def __init__(self, cmd, **kw):
            runs.append((cmd, kw.get("env", {})))

        def wait(self, timeout=None):
            return codes.pop(0) if codes else 0

    monkeypatch.setattr(boot.subprocess, "Popen", _P)
    return release, tmp_path / "apps", runs, codes


def test_release_layout(tmp_path):
    release = tmp_path / "release"
    m = _publish(tmp_path, release, "2.14.0")
    assert len(m["chunks"]) >= 2 and all(c["size"] <= 8000 for c in m["chunks"])
    assert (release / "app" / "manifest.json").is_file() and (release / "TreeAICoach.exe").is_file()
    v = json.loads((release / "version.json").read_text())
    assert v["version"] == "2.14.0" and v["sha256"] == m["launcher"]["sha256"] and v["size"] == 11
    boot.validate_manifest(json.loads((release / "app" / "manifest.json").read_text()))
    assert m["sha256"] == boot.manifest_sha("2.14.0", m["chunks"])
    _publish(tmp_path / "b", release, "2.15.0")
    assert sorted(p.name for p in (release / "app").iterdir()) == ["2.15.0", "manifest.json"]


def test_first_launch_installs_then_starts_without_network(env):
    release, apps, runs, _codes = env
    _publish(Path(release).parent.parent, release, "2.14.0")
    assert boot.main(["--selftest", "--selftest-out", "x.txt"]) == 0
    cmd, cenv = runs[-1]
    assert cmd[0] == str(apps / "2.14.0" / "TreeAICoach-app.exe") and cmd[1:] == ["--selftest", "--selftest-out", "x.txt"]
    assert cenv["TREEAICOACH_LAUNCHER"] == sys.executable and cenv["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert (apps / "2.14.0" / "_internal" / "sub" / "f5.bin").is_file()
    assert not (apps / ".download").exists()
    n = len(_Quiet.hits)
    assert boot.main([]) == 0                       # second launch: no network at all
    assert len(_Quiet.hits) == n and runs[-1][0][0] == cmd[0]


def test_offline_first_launch_fails_cleanly(env, monkeypatch):
    monkeypatch.setenv("TREEAICOACH_CHANNEL_URL", "http://127.0.0.1:9/release/version.json")
    assert boot.main([]) == 3
    assert env[2] == []


def test_update_keeps_previous_and_rolls_back_on_crash(env, tmp_path):
    release, apps, runs, codes = env
    _publish(tmp_path / "a", release, "2.14.0")
    assert boot.main([]) == 0
    _publish(tmp_path / "b", release, "2.15.0")
    codes.extend([1, 0])                            # the new version crashes at start, the old one works
    assert boot.main(["--tac-update"]) == 0
    assert [Path(c[0]).parent.name for c, _e in runs[-2:]] == ["2.15.0", "2.14.0"]
    st = boot.load_state(str(apps))
    assert st["current"] == "2.14.0" and st["bad"] == "2.15.0"


def test_update_confirmed_cleans_older_versions(env, tmp_path):
    release, apps, runs, _codes = env
    for i, v in enumerate(("2.13.0", "2.14.0", "2.15.0")):
        _publish(tmp_path / str(i), release, v)
        assert boot.main(["--tac-update"]) == 0
    assert sorted(p.name for p in apps.iterdir() if not p.name.startswith(".") and p.is_dir()) == ["2.14.0", "2.15.0"]
    st = boot.load_state(str(apps))
    assert st["current"] == "2.15.0" and st["unconfirmed"] is False


def test_resume_and_corrupt_chunk(env, tmp_path):
    release, apps, _runs, _codes = env
    m = _publish(tmp_path, release, "2.14.0")
    c = m["chunks"][0]
    dl = apps / ".download"
    dl.mkdir(parents=True)
    data = (release / "app" / c["name"]).read_bytes()
    (dl / (c["sha256"][:16] + ".zip.part")).write_bytes(data[:1000])
    ch = boot.Channel(os.environ["TREEAICOACH_CHANNEL_URL"])
    boot.install(ch, m, str(apps))
    assert any(True for _ in (apps / "2.14.0").iterdir())
    (release / "app" / c["name"]).write_bytes(b"x" * c["size"])           # server copy damaged
    m2 = dict(m, sha256="f" * 64, version="2.14.1")
    with pytest.raises(boot.BootError, match="abîmé"):
        boot.install(ch, m2, str(apps))


def test_install_self_replaces_running_launcher(env, tmp_path, monkeypatch):
    release, apps, runs, _codes = env
    _publish(tmp_path, release, "2.14.0")
    old = tmp_path / "Jeux" / "TreeAICoach.exe"
    old.parent.mkdir()
    old.write_bytes(b"MZ old launcher")
    status = tmp_path / "status.txt"
    assert boot.main(["--tac-install-self", str(old), "--tac-wait-pid", "0", "--tac-status", str(status)]) == 0
    assert old.read_bytes() == b"MZ launcher" and status.read_text().strip() == "ok"
    assert runs[-1][0] == [str(old)]                 # hands over to the installed launcher
    assert boot.is_installed(str(apps / "2.14.0"))


def test_validate_manifest_rejects_bad_input():
    good = {"version": "2.14.0", "exe": "TreeAICoach-app.exe", "sha256": "a" * 64,
            "chunks": [{"name": "2.14.0/part-01.zip", "size": 5, "sha256": "b" * 64}]}
    boot.validate_manifest(good)
    for bad in ({**good, "version": "x"}, {**good, "exe": "..\\evil.exe"}, {**good, "chunks": []},
                {**good, "chunks": [{"name": "../x.zip", "size": 5, "sha256": "b" * 64}]}, [], None):
        with pytest.raises(boot.BootError):
            boot.validate_manifest(bad)


def test_cleanup_keeps_recent_tmp(tmp_path):
    root = tmp_path / "apps"
    for n in ("2.13.1", "2.14.0", ".tmp-1-1", ".tmp-2-2"):
        (root / n).mkdir(parents=True)
    old = time.time() - 2 * boot.STALE_TMP_S
    os.utime(root / ".tmp-1-1", (old, old))
    assert sorted(boot.cleanup(str(root), [str(root / "2.14.0")])) == [".tmp-1-1", "2.13.1"]


def test_channel_with_token_uses_contents_api():
    ch = boot.Channel("", "tok")
    assert ch.url("app/manifest.json").startswith("https://api.github.com/repos/SosatLaZ/treeaicoach/contents/release/app/")
    assert ch.request(ch.url("x"))
    req = ch.request("https://example.com/x")
    assert "Authorization" not in req.headers


def test_launcher_exe_prefers_launcher(tmp_path, monkeypatch):
    from treeaicoach import paths

    stub = tmp_path / "TreeAICoach.exe"
    stub.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv(paths.LAUNCHER_ENV, str(stub))
    assert paths.launcher_exe() == stub
    monkeypatch.setenv(paths.LAUNCHER_ENV, str(tmp_path / "gone.exe"))
    assert paths.launcher_exe() == Path(sys.executable)


def test_in_app_update_hands_over_to_new_launcher(tmp_path, monkeypatch):
    from treeaicoach import paths, updater

    stub = tmp_path / "TreeAICoach.exe"
    stub.write_bytes(b"MZ old")
    new = tmp_path / "upd" / "TreeAICoach-2.14.0.exe"
    new.parent.mkdir()
    new.write_bytes(b"MZ new")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv(paths.LAUNCHER_ENV, str(stub))
    seen = {}
    res = updater.apply_update(new, popen=lambda cmd, **kw: seen.update(cmd=cmd, **kw), pid=1234)
    assert res.ok, res.message
    assert seen["cmd"][:5] == [str(new), "--tac-install-self", str(stub.resolve()), "--tac-wait-pid", "1234"]
    assert "TREEAICOACH_LAUNCHER" not in seen["env"]


def test_startup_marks():
    from treeaicoach import startup

    assert startup.mark("unit") >= 0 and "unit=" in startup.summary()
