"""Optional DirectML runtime of the detector: install, activation, provider choice, CPU fallback."""
from __future__ import annotations

import hashlib
import io
import sys
import types
import zipfile

import numpy as np
import pytest

from treeaicoach import gpu_runtime as gr


@pytest.fixture(autouse=True)
def _reset_state():
    gr.STATE["detector"] = ""
    yield
    gr.STATE["detector"] = ""


def _fake_wheel() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in gr._REQUIRED:
            z.writestr(f, b"x")
        z.writestr("onnxruntime/tools/big.py", b"skip me")
        z.writestr("../evil.py", b"no")
    return buf.getvalue()


class _Resp(io.BytesIO):
    headers = {"Content-Length": "0"}


def test_install_checks_sha_and_keeps_only_core(tmp_path, monkeypatch):
    data = _fake_wheel()
    assert not gr.install(tmp_path, opener=lambda u: _Resp(data))          # wrong SHA-256
    assert not gr.installed(tmp_path)
    monkeypatch.setattr(gr, "ORT_DML_SHA256", hashlib.sha256(data).hexdigest())
    assert gr.install(tmp_path, opener=lambda u: _Resp(data))
    assert gr.installed(tmp_path)
    d = gr.runtime_dir(tmp_path)
    assert not (d / "onnxruntime" / "tools").exists() and not (tmp_path / "evil.py").exists()
    assert not list(tmp_path.glob("*.whl")) and not list(tmp_path.glob("*.part"))


def test_download_failure_keeps_cpu(tmp_path):
    def boom(u):
        raise OSError("offline")
    assert not gr.install(tmp_path, opener=boom)
    gr.set_enabled(True, tmp_path)
    assert not gr.activate(tmp_path, platform="win32")
    assert gr.STATE["detector"] == "not installed"
    assert gr.detector_providers(["DmlExecutionProvider", "CPUExecutionProvider"]) == ["CPUExecutionProvider"]


def test_opt_in_only(tmp_path):
    assert not gr.enabled(tmp_path)
    assert not gr.activate(tmp_path, platform="win32") and gr.STATE["detector"] == "off"
    gr.STATE["detector"] = ""
    assert not gr.activate(tmp_path, platform="linux") and gr.STATE["detector"] == "not windows"
    assert gr.set_enabled(True, tmp_path) and gr.enabled(tmp_path)
    st = gr.status(tmp_path)
    assert st["enabled"] and not st["installed"]


def _installed_enabled(tmp_path, monkeypatch):
    data = _fake_wheel()
    monkeypatch.setattr(gr, "ORT_DML_SHA256", hashlib.sha256(data).hexdigest())
    assert gr.install(tmp_path, opener=lambda u: _Resp(data))
    gr.set_enabled(True, tmp_path)
    monkeypatch.delitem(sys.modules, "onnxruntime", raising=False)
    return gr.runtime_dir(tmp_path)


def test_activate_mocked_dml(tmp_path, monkeypatch):
    d = _installed_enabled(tmp_path, monkeypatch)
    fake = types.SimpleNamespace(__file__=str(d / "onnxruntime" / "__init__.py"), __version__="1.19.2",
                                 get_available_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"])
    try:
        assert gr.activate(tmp_path, platform="win32", importer=lambda: fake)
        assert gr.detector_providers(fake.get_available_providers()) == ["DmlExecutionProvider", "CPUExecutionProvider"]
    finally:
        while str(d) in sys.path:
            sys.path.remove(str(d))


def test_activate_failure_restores_cpu(tmp_path, monkeypatch):
    d = _installed_enabled(tmp_path, monkeypatch)

    def bad():
        raise ImportError("DLL load failed: dxcore.dll")
    assert not gr.activate(tmp_path, platform="win32", importer=bad)
    assert str(d) not in sys.path and gr.STATE["detector"].startswith("load failed")
    import onnxruntime  # the bundled CPU build still imports
    assert "CPUExecutionProvider" in onnxruntime.get_available_providers()


def _wrapped_ort(monkeypatch, *, dml_ok: bool):
    import onnxruntime as real

    class Sess:
        def __init__(self, model, sess_options=None, providers=None):
            if "DmlExecutionProvider" in (providers or []) and not dml_ok:
                raise RuntimeError("no DX12 device")
            self._s = real.InferenceSession(model, sess_options=sess_options, providers=["CPUExecutionProvider"])
            self._p = list(providers or [])
            self.fail = False

        def get_providers(self):
            return self._p

        def run(self, *a, **k):
            if self.fail:
                raise RuntimeError("device removed")
            return self._s.run(*a, **k)

        def __getattr__(self, k):
            return getattr(self._s, k)

    mod = types.SimpleNamespace(**{k: getattr(real, k) for k in ("SessionOptions", "ExecutionMode",
                                                                   "GraphOptimizationLevel")})
    mod.get_available_providers = lambda: ["DmlExecutionProvider", "CPUExecutionProvider"]
    mod.InferenceSession = Sess
    monkeypatch.setitem(sys.modules, "onnxruntime", mod)
    gr.STATE["detector"] = "dml"           # as if activate() succeeded


def test_detector_uses_dml_then_falls_back_on_errors(monkeypatch):
    from treeaicoach.detector import OnnxDetector
    _wrapped_ort(monkeypatch, dml_ok=True)
    det = OnnxDetector()
    assert det.provider == "DirectML"
    img = np.zeros((200, 200, 3), np.uint8)
    det.detect(img)
    det._sess.fail = True
    for _ in range(3):
        assert det.detect(img) == []
    assert det.provider == "CPU"
    assert isinstance(det.detect(img), list)


def test_detector_dml_init_failure_is_cpu(monkeypatch):
    from treeaicoach.detector import OnnxDetector
    _wrapped_ort(monkeypatch, dml_ok=False)
    det = OnnxDetector()
    assert det.provider == "CPU"


def test_default_detector_is_cpu():
    from treeaicoach.detector import OnnxDetector
    assert OnnxDetector().provider == "CPU"


def test_health_line_shows_gpu():
    from treeaicoach.ui_common import health_text as health_line
    h = {"detect_ms": {"p50": 12, "p95": 30}, "detector_provider": "DirectML"}
    assert "carte graphique" in health_line(h)[0]
    h["detector_provider"] = "CPU"
    assert "carte graphique" not in health_line(h)[0]


def test_activate_real_import_from_front_of_sys_path(tmp_path, monkeypatch):
    """Without an importer: the unpacked package in front of sys.path shadows the bundled one."""
    saved = {k: v for k, v in sys.modules.items() if k.startswith("onnxruntime")}
    d = _installed_enabled(tmp_path, monkeypatch)
    (d / "onnxruntime" / "__init__.py").write_text(
        "__version__ = '1.19.2'\n"
        "def get_available_providers():\n    return ['DmlExecutionProvider', 'CPUExecutionProvider']\n")
    try:
        assert gr.activate(tmp_path, platform="win32")
        assert str(d) in sys.modules["onnxruntime"].__file__
    finally:
        while str(d) in sys.path:
            sys.path.remove(str(d))
        gr._purge_ort()
        sys.modules.update(saved)
