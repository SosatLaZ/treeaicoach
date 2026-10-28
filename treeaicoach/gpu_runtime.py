"""Optional GPU runtimes, downloaded on demand (never bundled, never required).

**Detector (DirectML).** ``onnxruntime-directml`` 1.19.2 (same version as the bundled CPU
``onnxruntime``) runs the minimap CNN on any DirectX 12 GPU (GTX 1650 included) through the
``DmlExecutionProvider``. The official cp311 wheel is 22.5 MB (pinned URL + SHA-256); only its
``onnxruntime/__init__.py`` + ``capi/`` are kept (``onnxruntime_pybind11_state.pyd`` 22 MB,
``DirectML.dll`` 18.5 MB, ``onnxruntime.dll`` 18 MB: ~60 MB on disk) in
``cache/gpu/ort-dml-1.19.2``. The model (opset 17: Conv, Clip, Add, Resize, Sigmoid, Relu,
Softmax) only uses operators the DML EP of 1.19 implements.

The DML build *replaces* the ``onnxruntime`` package for the whole process (two copies of the
pybind11 module cannot live in one process: their C++ types clash), so :func:`activate` must run
before anything imports ``onnxruntime`` (``create_detector`` calls it first; the VAD imports it
later and asks for ``CPUExecutionProvider`` explicitly, which the DML build also has). It puts
the folder in front of ``sys.path`` (PyInstaller 6 resolves the bundled modules through a
``sys.path`` entry, so the front entry wins) and checks the import; on any failure the folder is
removed again and the bundled CPU ``onnxruntime`` is used. Opt-in only (:func:`set_enabled`):
measured on the CPU path, the CNN is ~3 ms of the ~42 ms a frame costs (roster matcher, ring
checks and tracker stay on the CPU), and during a game the GPU is busy rendering League, so the
gain is a few % of TreeAI's CPU at best.

**Whisper (CUDA) - not offered.** ``ctranslate2`` 4.4.0 (stt_local.WHEELS) has CUDA 12 built in
but needs cuBLAS 12 + cuDNN 8 next to it: ``cublas64_12.dll`` + ``cublasLt64_12.dll``
(397 MB compressed / 574 MB on disk) + ``cudnn_ops_infer64_8.dll`` + ``cudnn_cnn_infer64_8.dll``
(the Whisper encoder's convolutions; 413 MB / 673 MB) = ~810 MB to download, 1.25 GB on disk,
measured member by member in the official NVIDIA wheels (nvidia-cublas-cu12 12.4.5.8,
nvidia-cudnn-cu12 8.9.7.29). ctranslate2 >= 4.5 (cuDNN 9) is no smaller (~900 MB). Above the
~700 MB budget for a beginner's PC with little free disk, and Whisper ``small`` int8 on the CPU
only runs after push-to-talk; kept CPU only.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ORT_DML_VERSION = "1.19.2"
ORT_DML_URL = ("https://files.pythonhosted.org/packages/28/b5/0c82f5e13f39a3df747b08a27c86b42a509981182f3c16a8f77d05c6ea7f/"
               "onnxruntime_directml-1.19.2-cp311-cp311-win_amd64.whl")
ORT_DML_SHA256 = "04cb32c21d793c5da4ad02d733149cc0af5b8a12553dfb95022fabb0f618a991"
ORT_DML_MB = 22.5
DML_PROVIDER = "DmlExecutionProvider"
CPU_PROVIDER = "CPUExecutionProvider"
#: files that must be there for the runtime to count as installed
_REQUIRED = ("onnxruntime/__init__.py", "onnxruntime/capi/_pybind_state.py",
             "onnxruntime/capi/onnxruntime_pybind11_state.pyd", "onnxruntime/capi/DirectML.dll")
_SETTINGS = "gpu.json"

#: state of this process: "" (not tried), "dml" (DML build active), or why it is not
STATE: dict[str, str] = {"detector": ""}


def gpu_root(root: Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    from treeaicoach.paths import cache_dir  # noqa: PLC0415

    return Path(cache_dir()) / "gpu"


def runtime_dir(root: Path | None = None) -> Path:
    return gpu_root(root) / f"ort-dml-{ORT_DML_VERSION}"


def installed(root: Path | None = None) -> bool:
    d = runtime_dir(root)
    return all((d / f).is_file() for f in _REQUIRED)


def _settings(root: Path | None) -> dict[str, Any]:
    try:
        data = json.loads((gpu_root(root) / _SETTINGS).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def enabled(root: Path | None = None) -> bool:
    """The user asked for the GPU detector ("Utiliser la carte graphique")."""
    return bool(_settings(root).get("detector", False))


def set_enabled(on: bool, root: Path | None = None) -> bool:
    """Persist the choice (applies at the next start of the app). Never raises."""
    try:
        g = gpu_root(root)
        g.mkdir(parents=True, exist_ok=True)
        data = _settings(root)
        data["detector"] = bool(on)
        tmp = g / (_SETTINGS + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, g / _SETTINGS)
        return True
    except Exception:
        log.warning("GPU : réglage non enregistré", exc_info=True)
        return False


def _fetch(url: str, sha256: str, dest: Path, opener: Callable[[str], Any] | None,
           progress: Callable[[int], None] | None) -> bool:
    part = dest.with_name(dest.name + ".part")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if opener is None:
            import urllib.request  # noqa: PLC0415

            opener = lambda u: urllib.request.urlopen(u, timeout=60)  # noqa: E731,S310 (fixed https URL)
        h = hashlib.sha256()
        with opener(url) as resp, open(part, "wb") as fh:
            try:
                total = int(resp.headers.get("Content-Length", 0) or 0)
            except Exception:
                total = 0
            got = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if progress is not None and total:
                    progress(int(100 * got / total))
        if h.hexdigest() != sha256:
            log.warning("GPU : somme de contrôle invalide pour %s", url.rsplit("/", 1)[-1])
            return False
        os.replace(part, dest)
        return True
    except Exception:
        log.warning("GPU : téléchargement impossible (%s)", url.rsplit("/", 1)[-1], exc_info=True)
        return False
    finally:
        try:
            part.unlink(missing_ok=True)
        except Exception:
            pass


def _keep(member: str) -> bool:
    if ".." in member or member.startswith("/") or member.endswith("/"):
        return False
    return member == "onnxruntime/__init__.py" or member.startswith("onnxruntime/capi/")


def install(root: Path | None = None, opener: Callable[[str], Any] | None = None,
            progress: Callable[[int], None] | None = None) -> bool:
    """Download (22.5 MB, SHA-256 checked) and unpack the DirectML runtime once. Never raises."""
    if installed(root):
        return True
    d = runtime_dir(root)
    tmp = d.with_name(d.name + ".part")
    whl = gpu_root(root) / ORT_DML_URL.rsplit("/", 1)[-1]
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        if not _fetch(ORT_DML_URL, ORT_DML_SHA256, whl, opener, progress):
            return False
        with zipfile.ZipFile(whl) as zf:
            zf.extractall(tmp, [m for m in zf.namelist() if _keep(m)])
        if not all((tmp / f).is_file() for f in _REQUIRED):
            log.warning("GPU : paquet DirectML incomplet")
            return False
        shutil.rmtree(d, ignore_errors=True)
        tmp.replace(d)
        log.info("GPU : moteur DirectML %s installé (%s)", ORT_DML_VERSION, d)
        return True
    except Exception:
        log.warning("GPU : installation du moteur DirectML impossible", exc_info=True)
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            whl.unlink(missing_ok=True)
        except Exception:
            pass


def _purge_ort() -> None:
    for k in [k for k in sys.modules if k == "onnxruntime" or k.startswith("onnxruntime.")]:
        sys.modules.pop(k, None)


def activate(root: Path | None = None, *, platform: str | None = None,
             importer: Callable[[], Any] | None = None) -> bool:
    """Make ``import onnxruntime`` load the DirectML build when the user enabled it and it is
    installed. True when the DML build is the active ``onnxruntime``. Never raises; on failure
    the bundled CPU build stays (``STATE["detector"]`` says why). Idempotent."""
    if STATE["detector"] == "dml":
        return True
    if STATE["detector"]:
        return False
    try:
        if (platform or sys.platform) != "win32":
            STATE["detector"] = "not windows"
            return False
        if not enabled(root):
            STATE["detector"] = "off"
            return False
        if not installed(root):
            STATE["detector"] = "not installed"
            return False
        d = runtime_dir(root)
        mod = sys.modules.get("onnxruntime")
        if mod is not None:
            # already imported: fine if it is ours, too late otherwise (restart needed)
            ok = str(d) in str(getattr(mod, "__file__", "") or "")
            STATE["detector"] = "dml" if ok else "cpu onnxruntime already loaded"
            return ok
        sys.path.insert(0, str(d))
        dll_handle = None
        if hasattr(os, "add_dll_directory"):
            try:
                dll_handle = os.add_dll_directory(str(d / "onnxruntime" / "capi"))
            except Exception:
                dll_handle = None
        try:
            if importer is not None:
                ort = importer()
            else:
                import onnxruntime as ort  # noqa: PLC0415
            provs = list(ort.get_available_providers())
            if DML_PROVIDER not in provs or str(d) not in str(getattr(ort, "__file__", "") or ""):
                raise RuntimeError(f"DirectML build not active (providers {provs})")
        except Exception as exc:
            try:
                sys.path.remove(str(d))
            except ValueError:
                pass
            if dll_handle is not None:
                try:
                    dll_handle.close()
                except Exception:
                    pass
            _purge_ort()
            STATE["detector"] = f"load failed: {exc}"[:200]
            log.warning("GPU : moteur DirectML inutilisable (%s), détection sur le processeur", exc)
            return False
        STATE["detector"] = "dml"
        log.info("GPU : onnxruntime DirectML %s actif", getattr(ort, "__version__", "?"))
        return True
    except Exception as exc:
        STATE["detector"] = f"error: {exc}"[:200]
        log.warning("GPU : activation impossible", exc_info=True)
        return False


def detector_providers(available: list[str] | tuple[str, ...]) -> list[str]:
    """Execution providers to ask for, best first; CPU always last (fallback)."""
    out = [DML_PROVIDER] if STATE["detector"] == "dml" and DML_PROVIDER in available else []
    return out + [CPU_PROVIDER]


def status(root: Path | None = None) -> dict[str, Any]:
    """For the settings page / diagnostics. Never raises."""
    try:
        return {"enabled": enabled(root), "installed": installed(root), "state": STATE["detector"] or "not tried",
                "download_mb": ORT_DML_MB, "version": ORT_DML_VERSION}
    except Exception:
        return {"enabled": False, "installed": False, "state": "error"}


__all__ = ["activate", "install", "installed", "enabled", "set_enabled", "status", "detector_providers",
           "DML_PROVIDER", "CPU_PROVIDER", "STATE"]
