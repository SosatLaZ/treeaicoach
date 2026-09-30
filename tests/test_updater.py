"""Tests for treeaicoach.updater against a local http.server (fake version.json / exe)."""

from __future__ import annotations

import hashlib
import http.server
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from treeaicoach import updater
from treeaicoach.config import Config

EXE = b"MZ" + bytes(range(256)) * 900   # ~230 kB fake exe
SHA = hashlib.sha256(EXE).hexdigest()


class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, bytes, dict[str, str]]] = {}
    seen: list[tuple[str, dict[str, str]]] = []

    def do_GET(self) -> None:  # noqa: N802
        type(self).seen.append((self.path, dict(self.headers)))
        code, body, headers = type(self).routes.get(self.path, (404, b"not found", {}))
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture()
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path / "home"))
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    routes: dict[str, tuple[int, bytes, dict[str, str]]] = {}
    seen: list = []
    handler = type("H", (_Handler,), {"routes": routes, "seen": seen})
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield base, routes, seen
    finally:
        httpd.shutdown()
        httpd.server_close()


def _manifest(version: str = "9.9.9", **kw: Any) -> bytes:
    d = {"version": version, "sha256": SHA, "size": len(EXE), "notes": "Corrections"}
    d.update(kw)
    return json.dumps(d).encode()


def test_parse_and_compare_versions():
    assert updater.parse_version("1.4.0") == (1, 4, 0, 0, 1)
    assert updater.parse_version("v1.4") == (1, 4, 0, 0, 1)
    assert updater.parse_version("abc") is None and updater.parse_version(None) is None
    assert updater.is_newer("1.4.1", "1.4.0")
    assert updater.is_newer("1.10.0", "1.9.9")        # semantic, not lexicographic
    assert not updater.is_newer("1.4.0", "1.4.0")
    assert not updater.is_newer("1.3.9", "1.4.0")
    assert updater.is_newer("1.4.0", "1.4.0-beta")
    assert not updater.is_newer("garbage", "1.0.0")


def test_config_fields_backward_compatible():
    c = Config.from_dict({"voice_rate": 3})
    assert c.update_channel_url == "" and c.github_token == "" and c.check_updates_on_start is True
    c = Config.from_dict({"github_token": "  ghp_abc\n", "update_channel_url": 5, "check_updates_on_start": 0})
    assert c.github_token == "ghp_abc" and c.update_channel_url == "" and c.check_updates_on_start is False


def test_resolve_channel_default_and_api():
    ch = updater.resolve_channel(Config())
    assert ch.manifest_url == updater.DEFAULT_CHANNEL_URL and not ch.api
    assert ch.exe_url.endswith("/release/TreeAICoach.exe")
    ch = updater.resolve_channel(Config(github_token="tok"))
    assert ch.api and ch.manifest_url.startswith("https://api.github.com/repos/SosatLaZ/treeaicoach/contents/")
    assert "ref=claude-team%2Fbrave-mendel-j8fqkf" in ch.manifest_url
    req = updater._request(ch.manifest_url, "tok", True)
    assert req.get_header("Authorization") == "Bearer tok"
    assert req.get_header("Accept") == "application/vnd.github.raw"
    # the token never leaves GitHub hosts
    assert updater._request("https://evil.example/x", "tok", False).get_header("Authorization") is None


def test_check_up_to_date_and_available(server):
    base, routes, _ = server
    routes["/r/version.json"] = (200, _manifest("1.4.0"), {})
    res = updater.check_for_update(url=base + "/r/version.json", current="1.4.0")
    assert res.status == updater.UP_TO_DATE and "Déjà à jour" in res.message
    res = updater.check_for_update(url=base + "/r/version.json", current="1.3.0")
    assert res.available and res.info.version == "1.4.0"
    assert "Nouvelle version 1.4.0 disponible" in res.message
    assert res.can_install is False   # tests run from source


@pytest.mark.parametrize("code,token,expected", [
    (404, "", "dépôt privé, ajoute un jeton GitHub"),
    (401, "", "dépôt privé, ajoute un jeton GitHub"),
    (401, "t", "Jeton GitHub refusé"),
    (403, "t", "n'a pas le droit"),
    (500, "", "indisponible"),
])
def test_check_http_errors(server, code, token, expected):
    base, routes, _ = server
    routes["/v.json"] = (code, b"x", {})
    res = updater.check_for_update(url=base + "/v.json", token=token)
    assert res.status == updater.ERROR and expected in res.message


def test_check_bad_manifest_and_unreachable(server):
    base, routes, _ = server
    routes["/a.json"] = (200, b"{not json", {})
    routes["/b.json"] = (200, json.dumps({"version": "2.0", "sha256": "zz", "size": 3}).encode(), {})
    assert "illisible" in updater.check_for_update(url=base + "/a.json").message
    assert "SHA-256" in updater.check_for_update(url=base + "/b.json").message
    res = updater.check_for_update(url="http://127.0.0.1:1/v.json", timeout=2)
    assert res.status == updater.ERROR and "Impossible de joindre" in res.message


def test_token_sent_to_api_and_stripped_on_foreign_redirect(server, monkeypatch):
    base, routes, seen = server
    monkeypatch.setattr(updater, "GITHUB_API", base)
    monkeypatch.setattr(updater, "TOKEN_HOSTS", {"127.0.0.1"})
    api_manifest = "/repos/SosatLaZ/treeaicoach/contents/release/version.json?ref=claude-team%2Fbrave-mendel-j8fqkf"
    api_exe = "/repos/SosatLaZ/treeaicoach/contents/release/TreeAICoach.exe?ref=claude-team%2Fbrave-mendel-j8fqkf"
    routes[api_manifest] = (200, _manifest("5.0.0"), {})
    routes[api_exe] = (302, b"", {"Location": base.replace("127.0.0.1", "localhost") + "/blob"})
    routes["/blob"] = (200, EXE, {})
    cfg = Config(github_token="secret")
    res = updater.check_for_update(cfg, current="1.0.0")
    assert res.available, res.message
    hdrs = {k.lower(): v for k, v in seen[-1][1].items()}
    assert hdrs["authorization"] == "Bearer secret" and hdrs["accept"] == "application/vnd.github.raw"
    dl = updater.download_update(res.info, cfg)
    assert dl.ok, dl.message
    blob_headers = {k.lower() for k in seen[-1][1]}
    assert seen[-1][0] == "/blob" and "authorization" not in blob_headers


def test_download_verify_progress_and_reuse(server, tmp_path):
    base, routes, seen = server
    routes["/r/version.json"] = (200, _manifest("2.0.0"), {})
    routes["/r/TreeAICoach.exe"] = (200, EXE, {})
    res = updater.check_for_update(url=base + "/r/version.json", current="1.0.0")
    calls: list[tuple[int, int]] = []
    dest = tmp_path / "upd"
    (dest).mkdir()
    (dest / "TreeAICoach-1.5.0.exe").write_bytes(b"old")
    dl = updater.download_update(res.info, url=base + "/r/version.json", dest_dir=dest,
                                 progress=lambda d, t: calls.append((d, t)))
    assert dl.ok and dl.path == dest / "TreeAICoach-2.0.0.exe"
    assert dl.path.read_bytes() == EXE
    assert calls[-1] == (len(EXE), len(EXE)) and len(calls) > 1
    assert not (dest / "TreeAICoach-1.5.0.exe").exists()          # old downloads cleaned
    assert not list(dest.glob("*.part"))
    n = len(seen)
    dl2 = updater.download_update(res.info, url=base + "/r/version.json", dest_dir=dest)
    assert dl2.ok and "déjà téléchargée" in dl2.message and len(seen) == n   # no new request


def test_download_default_dir_and_direct_url(server):
    base, routes, _ = server
    routes["/elsewhere.bin"] = (200, EXE, {})
    info = updater.UpdateInfo("3.1.0", SHA, len(EXE), url=base + "/elsewhere.bin")
    dl = updater.download_update(info, url=base + "/nothing/version.json")
    assert dl.ok, dl.message
    assert dl.path.parent == updater.updates_dir() and dl.path.name == "TreeAICoach-3.1.0.exe"


def test_download_rejects_corrupt_and_oversized(server, tmp_path):
    base, routes, _ = server
    routes["/r/TreeAICoach.exe"] = (200, EXE[:-1] + b"X", {})
    info = updater.UpdateInfo("2.0.0", SHA, len(EXE))
    dl = updater.download_update(info, url=base + "/r/version.json", dest_dir=tmp_path)
    assert not dl.ok and "corrompu" in dl.message
    assert not list(tmp_path.iterdir())
    routes["/r/TreeAICoach.exe"] = (200, EXE + b"extra", {})
    dl = updater.download_update(info, url=base + "/r/version.json", dest_dir=tmp_path)
    assert not dl.ok and "plus gros" in dl.message
    routes["/r/TreeAICoach.exe"] = (404, b"", {})
    dl = updater.download_update(info, url=base + "/r/version.json", dest_dir=tmp_path)
    assert not dl.ok and "jeton" in dl.message


def test_download_cancel(server, tmp_path):
    base, routes, _ = server
    routes["/r/TreeAICoach.exe"] = (200, EXE, {})
    ev = threading.Event()
    ev.set()
    info = updater.UpdateInfo("2.0.0", SHA, len(EXE))
    dl = updater.download_update(info, url=base + "/r/version.json", dest_dir=tmp_path, cancel=ev)
    assert not dl.ok and "annulé" in dl.message


def test_apply_from_source_only_reports(tmp_path):
    exe = tmp_path / "new.exe"
    exe.write_bytes(EXE)
    res = updater.apply_update(exe)
    assert not res.ok and "sources" in res.message


def test_apply_writes_script_and_launches_detached(tmp_path):
    new = tmp_path / "upd" / "TreeAICoach-2.0.0.exe"
    new.parent.mkdir()
    new.write_bytes(EXE)
    target = tmp_path / "app" / "TreeAICoach.exe"
    target.parent.mkdir()
    target.write_bytes(b"old")
    launched: list[tuple[list[str], dict]] = []
    info = updater.UpdateInfo("2.0.0", SHA, len(EXE))
    res = updater.apply_update(new, info, target=target, pid=4242,
                               popen=lambda cmd, **kw: launched.append((cmd, kw)))
    assert res.ok and "redémarrer" in res.message
    script = res.script
    assert script is not None and script.exists() and script.parent == new.parent
    text = script.read_bytes()
    text.decode("ascii")                                  # ASCII only
    assert b"\r\n" in text and b"%TAC_PID%" in text and b'start "" "%TAC_DST%"' in text
    cmd, kw = launched[0]
    assert cmd[-1] == str(script)
    env = kw["env"]
    assert env["TAC_PID"] == "4242" and env["TAC_SRC"] == str(new) and env["TAC_DST"] == str(target.resolve())
    assert kw["creationflags"] & 0x08000000                # CREATE_NO_WINDOW


def test_apply_rejects_corrupt_file_and_falls_back_without_breakaway(tmp_path):
    new = tmp_path / "n.exe"
    new.write_bytes(b"bad")
    info = updater.UpdateInfo("2.0.0", SHA, len(EXE))
    res = updater.apply_update(new, info, target=tmp_path / "t.exe", popen=lambda *a, **k: None)
    assert not res.ok and "corrompu" in res.message
    calls: list[int] = []

    def popen(cmd, **kw):
        calls.append(kw["creationflags"])
        if kw["creationflags"] & 0x01000000:
            raise PermissionError("breakaway denied")

    res = updater.apply_update(new, target=tmp_path / "t.exe", popen=popen)
    assert res.ok and len(calls) == 2


def test_script_env_strips_pyinstaller_vars(tmp_path):
    env = updater.script_env(tmp_path / "a.exe", tmp_path / "b.exe", 7,
                             base={"PATH": "x", "_MEIPASS2": "y", "_PYI_ARCHIVE_FILE": "z"})
    assert "_MEIPASS2" not in env and "_PYI_ARCHIVE_FILE" not in env and env["PATH"] == "x"
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_write_manifest_roundtrip(tmp_path):
    exe = tmp_path / "TreeAICoach.exe"
    exe.write_bytes(EXE)
    out = updater.write_manifest(exe, tmp_path / "version.json", version="1.2.3", notes="n")
    info = updater.parse_manifest(json.loads(out.read_text(encoding="utf-8")))
    assert info == updater.UpdateInfo("1.2.3", SHA, len(EXE), "n", "")


def test_published_manifest_matches_release_exe():
    root = Path(__file__).resolve().parent.parent
    man = root / "release" / "version.json"
    exe = root / "release" / "TreeAICoach.exe"
    if not man.exists() or not exe.exists():
        pytest.skip("no release build in this checkout")
    info = updater.parse_manifest(json.loads(man.read_text(encoding="utf-8")))
    assert exe.stat().st_size == info.size


# --------------------------------------------------------------------------- UI (Réglages → Mises à jour)


def test_ui_updates_section(tmp_path, monkeypatch):
    import test_ui as tu
    if not tu._display_ok():
        pytest.skip("no display / Tk available")
    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path / "home"))
    from treeaicoach import paths
    paths._reset_cache()
    info = updater.UpdateInfo("9.0.0", SHA, len(EXE))
    monkeypatch.setattr(updater, "check_for_update", lambda cfg=None, **k: updater.CheckResult(
        updater.AVAILABLE, "Nouvelle version 9.0.0 disponible (tu as la 1.0.0).", "1.0.0", info, True))
    app, _voice, _ = tu._build(tmp_path)
    try:
        app.show_page("settings")
        assert app._update_btn.cget("state") == "disabled"
        app._update_token_entry.insert(0, "tok123")
        app.check_updates()
        tu._pump(app, 3.0, lambda: not app._update_busy)
        assert "Nouvelle version 9.0.0" in app._update_status.cget("text")
        assert app._update_btn.cget("state") == "normal" and app.cfg.github_token == "tok123"
        app._startup_update_check()                                 # quiet: toast only
        tu._pump(app, 3.0, lambda: not app._update_busy)

        progress_seen: list[int] = []

        def fake_download(i, cfg=None, progress=None, **k):
            for d in (0, i.size // 2, i.size):
                progress(d, i.size)
                progress_seen.append(d)
            return updater.DownloadResult(False, "Téléchargement impossible : test")

        monkeypatch.setattr(updater, "download_update", fake_download)
        app.install_update()
        tu._pump(app, 3.0, lambda: not app._update_busy)
        assert "test" in app._update_status.cget("text") and progress_seen
        assert app._update_btn.cget("state") == "normal" and not app._closing

        monkeypatch.setattr(updater, "download_update",
                            lambda i, cfg=None, **k: updater.DownloadResult(True, "ok", tmp_path / "x.exe"))
        monkeypatch.setattr(updater, "apply_update",
                            lambda p, i=None, **k: updater.ApplyResult(True, "Installation : redémarrage."))
        app.install_update()
        tu._pump(app, 3.0, lambda: app._closing)
        assert app._closing
    finally:
        app.close()
