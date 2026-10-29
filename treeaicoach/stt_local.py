"""Local Whisper transcription (free, offline, zero API request): OpenAI Whisper « small » converted for
CTranslate2 (``Systran/faster-whisper-small``, int8 on the CPU), French forced, a League vocabulary
prompt (« Hey coach », gank, Baron, drake, the champions of the game…).

Nothing is bundled in the exe (size): on first use the CTranslate2 engine and the ``tokenizers`` wheels
(official PyPI Windows wheels, ~21 MB) and the model files (~490 MB, Hugging Face, pinned revision) are
downloaded once into ``cache_dir()/voice`` and checked by SHA-256. We do NOT use the ``faster-whisper``
package itself (it drags PyAV / huggingface_hub / requests): the ~100 lines it needs (log-mel features,
prompt tokens, decoding) are here, numpy only.

:func:`transcriber` returns the process-wide :class:`WhisperSTT`; :meth:`WhisperSTT.transcribe` takes
16 kHz mono int16 PCM (one VAD-cut utterance, <= :data:`MAX_UTTERANCE_S`) and returns the French text
("" when unavailable / nothing said). Never raises."""

from __future__ import annotations

import hashlib
import io
import logging
import os
import shutil
import sys
import threading
import time
import zipfile
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
MAX_UTTERANCE_S = 15.0
CPU_THREADS = 2

#: official Windows wheels (cp311), fetched once, checked by SHA-256 (``import_vosk`` pattern)
WHEELS: tuple[tuple[str, str, str], ...] = (
    ("ctranslate2", "https://files.pythonhosted.org/packages/01/2c/93bb6741ee904b4c43e81b67e1b8b30f14de3c49ac3ac6cf97c54c6fcdcf/"
     "ctranslate2-4.4.0-cp311-cp311-win_amd64.whl",
     "20dd98ccb48cc4a20d65a8ab0f79cbb92f95d3d92969874332f39d737b36b9ca"),
    ("tokenizers", "https://files.pythonhosted.org/packages/75/68/1b4f928b15a36ed278332ac75d66d7eb65d865bf344d049c452c18447bf9/"
     "tokenizers-0.20.3-cp311-none-win_amd64.whl",
     "88301aa0801f225725b6df5dea3d77c80365ff2362ca7e252583f2b4809c4cc0"),
)
ENGINE_DIR_NAME = "whisper-engine-ct2-4.4.0"

#: model files of a pinned Hugging Face revision (sha256 of each file)
MODELS: dict[str, tuple[str, int, dict[str, str]]] = {
    "small": ("536b0662742c02347bc0e980a01041f333bce120", 490, {
        "config.json": "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
        "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
        "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
        "model.bin": "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"}),
    "base": ("ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66", 150, {
        "config.json": "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a",
        "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
        "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
        "model.bin": "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9"}),
}
DEFAULT_MODEL = "small"
HF_URL = "https://huggingface.co/Systran/faster-whisper-{name}/resolve/{rev}/{file}"

#: League words the French model would otherwise misspell (« ganque », « barre-on »…)
LOL_VOCAB = ("Hey coach, gank, Baron Nashor, drake, dragon, Héraut, larves, ward, balise, top, mid, jungle, "
             "ADC, support, flash, ulti, teleport, ignite, tower, inhib, back, push, farm, roam, Garen, Swain")


#: Measured 2026-10-24 (4-core Xeon 2.1 GHz, int8, 48 French League questions of ~3 s, 4 voices, clean and
#: with noise at 15 dB SNR; WER = word error rate):
#:   30 s window : base 1.5-1.7 s / small 3.6-4.1 s per question (too slow)
#:   trimmed     : base beam 3 0.37 s, WER 20 % clean / 26 % noisy (too many wrong words)
#:                 small beam 1 4 threads 0.76 s, WER 7 / 11 % ; small beam 3 4 threads 1.09 s, WER 5 / 8 %
#:                 small beam 3 2 threads 0.72 s (p90 0.86 s), WER 5 / 8 %  <- chosen
#:   3 s clip     : small 0.67-0.70 s, base 0.19-0.24 s (target < 1.5 s on a mid 4-core CPU: small fits)
#: 2 threads beat 4 (less contention, and the game keeps two cores).
LATENCY_NOTE = "small, trimmed input, beam 3, 2 threads: ~0.7 s for a 3 s question"

#: the encoder reads the utterance + PAD_S of silence, not Whisper's fixed 30 s window (CTranslate2 >= 4
#: accepts shorter inputs): x3 to x5 faster on a short question, same transcript (measured, see
#: :data:`LATENCY_NOTE`). ``TRIM_INPUT = False`` restores the 30 s window.
TRIM_INPUT = True
PAD_S = 1.0
MIN_FRAMES = 300
BEAM_SIZE = 3


def input_frames(samples: int) -> int:
    """Mel frames (10 ms each) given to the encoder for ``samples`` of 16 kHz audio: rounded up to 1 s."""
    if not TRIM_INPUT:
        return 3000
    n = int(samples / 160 + PAD_S * 100)
    return max(MIN_FRAMES, min(3000, -(-n // 100) * 100))


# ------------------------------------------------------------------ features (numpy only)
def mel_filters(sr: int = SAMPLE_RATE, n_fft: int = 400, n_mels: int = 80) -> Any:
    """Slaney-scale mel filterbank, identical to Whisper's (librosa ``filters.mel`` defaults)."""
    import numpy as np  # noqa: PLC0415

    freqs = np.fft.rfftfreq(n=n_fft, d=1.0 / sr)
    f_sp, min_log_hz = 200.0 / 3, 1000.0
    min_log_mel, logstep = min_log_hz / f_sp, np.log(6.4) / 27.0
    max_mel = min_log_mel + np.log((sr / 2) / min_log_hz) / logstep
    mels = np.linspace(0.0, max_mel, n_mels + 2)
    hz = f_sp * mels
    log_t = mels >= min_log_mel
    hz[log_t] = min_log_hz * np.exp(logstep * (mels[log_t] - min_log_mel))
    fdiff = np.diff(hz)
    ramps = hz[:, None] - freqs[None, :]
    lower = -ramps[:-2] / fdiff[:-1, None]
    upper = ramps[2:] / fdiff[1:, None]
    w = np.maximum(0.0, np.minimum(lower, upper))
    w *= (2.0 / (hz[2:n_mels + 2] - hz[:n_mels]))[:, None]
    return w.astype(np.float32)


def log_mel(audio: Any, n_mels: int = 80, frames: int = 3000) -> Any:
    """Whisper input features ``(n_mels, frames)`` of float32 audio in [-1, 1] (padded to 30 s)."""
    import numpy as np  # noqa: PLC0415

    n_fft, hop = 400, 160
    a = np.zeros(frames * hop, dtype=np.float32)
    x = np.asarray(audio, dtype=np.float32)[: frames * hop]
    a[: x.size] = x
    a = np.pad(a, n_fft // 2, mode="reflect")
    win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n_fft) / n_fft)).astype(np.float32)   # periodic Hann
    idx = np.arange(n_fft)[None, :] + hop * np.arange(frames + 1)[:, None]
    spec = np.fft.rfft(a[idx] * win, axis=1)
    mag = (np.abs(spec[:-1]) ** 2).T                               # drop the last frame (as torch.stft)
    mel = mel_filters(n_mels=n_mels) @ mag
    lg = np.log10(np.maximum(mel, 1e-10))
    lg = np.maximum(lg, lg.max() - 8.0)
    return ((lg + 4.0) / 4.0).astype(np.float32)


def pcm_to_float(pcm: bytes) -> Any:
    import numpy as np  # noqa: PLC0415

    return np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0


def build_prompt(names: Iterable[str] = (), vocab: str = LOL_VOCAB, max_chars: int = 600) -> str:
    """The Whisper ``initial_prompt``: the champions of the current game FIRST (the words a question names
    most), then the League vocabulary (deduplicated, cut at ``max_chars`` on a word boundary)."""
    seen: list[str] = []
    for n in names:
        n = str(n or "").strip()
        if n and n.lower() not in (s.lower() for s in seen):
            seen.append(n)
    vocab_words = [w.strip() for w in vocab.split(",") if w.strip() and w.strip().lower() not in
                   (s.lower() for s in seen)]
    words = seen + vocab_words
    txt = ""
    for w in words:
        nxt = (txt + ", " + w) if txt else w
        if len(nxt) + 1 > max_chars:
            break
        txt = nxt
    return txt + "."


# ------------------------------------------------------------------ download (once, pinned)
def _fetch(url: str, sha256: str, dest: Path, opener: Callable[[str], Any] | None = None,
           progress: Callable[[int], None] | None = None) -> bool:
    """Stream ``url`` to ``dest`` (atomic), True when its SHA-256 matches. Never raises."""
    if dest.is_file():
        return True
    part = dest.with_name(dest.name + ".part")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if opener is None:
            import urllib.request  # noqa: PLC0415

            opener = lambda u: urllib.request.urlopen(u, timeout=60)  # noqa: E731,S310 (fixed https URLs)
        h = hashlib.sha256()
        with opener(url) as resp, open(part, "wb") as fh:
            total = 0
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
            log.warning("Whisper : somme de contrôle invalide pour %s", url.rsplit("/", 1)[-1])
            return False
        os.replace(part, dest)
        return True
    except Exception:
        log.warning("Whisper : téléchargement impossible (%s)", url.rsplit("/", 1)[-1], exc_info=True)
        return False
    finally:
        try:
            part.unlink(missing_ok=True)
        except Exception:
            pass


def voice_cache() -> Path:
    from treeaicoach.paths import cache_dir  # noqa: PLC0415

    return Path(cache_dir()) / "voice"


def model_path(name: str = DEFAULT_MODEL, root: Path | None = None) -> Path:
    rev = MODELS[name][0]
    return (root or voice_cache()) / f"faster-whisper-{name}-{rev[:8]}"


def model_ready(name: str = DEFAULT_MODEL, root: Path | None = None) -> bool:
    p = model_path(name, root)
    return all((p / f).is_file() for f in MODELS[name][2])


def download_model(name: str = DEFAULT_MODEL, root: Path | None = None, opener: Callable[[str], Any] | None = None,
                   progress: Callable[[int], None] | None = None) -> bool:
    rev, _mb, files = MODELS[name]
    p = model_path(name, root)
    for f in sorted(files, key=lambda f: f == "model.bin"):     # the big one last
        if not _fetch(HF_URL.format(name=name, rev=rev, file=f), files[f], p / f, opener,
                      progress if f == "model.bin" else None):
            return False
    return True


def _stub_missing(names: Sequence[str]) -> None:
    """ctranslate2's converters import ``yaml`` / ``tqdm`` at the top (never used for inference): empty
    stand-ins when the exe lacks them."""
    import importlib.util  # noqa: PLC0415
    import types  # noqa: PLC0415

    for name in names:
        try:
            if name in sys.modules or importlib.util.find_spec(name) is not None:
                continue
        except Exception:
            pass
        mod = types.ModuleType(name)
        if name == "tqdm":
            mod.tqdm = object  # type: ignore[attr-defined]
        sys.modules[name] = mod


def _shim_pkg_resources() -> None:
    """ctranslate2 4.x on Windows runs ``import pkg_resources; pkg_resources.resource_filename(__name__, "")``
    at import (to find its DLLs). The frozen exe has no setuptools: without this stand-in the import failed
    with ``ModuleNotFoundError: pkg_resources`` and Whisper NEVER loaded on the user's PC (Vosk only).
    The stand-in answers ``resource_filename`` from the package's own folder (importlib)."""
    try:
        import pkg_resources  # noqa: F401, PLC0415

        return
    except Exception:
        pass
    import importlib.util  # noqa: PLC0415
    import types  # noqa: PLC0415

    def resource_filename(package: str, resource: str = "") -> str:
        spec = importlib.util.find_spec(str(package))
        if spec is None:
            raise ImportError(f"no package {package!r}")
        locs = list(spec.submodule_search_locations or [])
        base = locs[0] if locs else os.path.dirname(str(spec.origin or ""))
        return os.path.join(base, resource) if resource else base

    mod = types.ModuleType("pkg_resources")
    mod.resource_filename = resource_filename  # type: ignore[attr-defined]
    mod.__treeai_shim__ = True  # type: ignore[attr-defined]
    sys.modules["pkg_resources"] = mod


def _engine_error(exc: BaseException) -> str:
    """« ModuleNotFoundError: No module named 'x' » (short, no user path) for the UI and the logs."""
    msg = str(exc).replace(str(Path.home()), "~")
    return f"{type(exc).__name__}: {msg}"[:160] if msg else type(exc).__name__


def import_engine(root: Path | None = None, opener: Callable[[str], Any] | None = None) -> tuple[Any, Any] | None:
    """``(ctranslate2, tokenizers)``: installed, else the pinned Windows wheels downloaded once. None on failure
    (:data:`ENGINE_ERROR` then tells why)."""
    global ENGINE_ERROR
    _stub_missing(("yaml", "tqdm"))
    _shim_pkg_resources()
    try:
        import ctranslate2  # noqa: PLC0415
        import tokenizers  # noqa: PLC0415

        ENGINE_ERROR = ""
        return ctranslate2, tokenizers
    except Exception as exc:
        if sys.platform != "win32" and opener is None:
            ENGINE_ERROR = _engine_error(exc)
            return None
    try:
        base = (root or voice_cache()) / ENGINE_DIR_NAME
        if not all((base / pkg / "__init__.py").exists() for pkg, _u, _s in WHEELS):
            tmp = base.with_name(base.name + ".part")
            shutil.rmtree(tmp, ignore_errors=True)
            for pkg, url, sha in WHEELS:
                whl = tmp.parent / url.rsplit("/", 1)[-1]
                if not _fetch(url, sha, whl, opener):
                    ENGINE_ERROR = ""             # a download problem, not a broken engine
                    return None
                with zipfile.ZipFile(whl) as zf:
                    members = [m for m in zf.namelist() if m.startswith(pkg + "/") and ".." not in m]
                    zf.extractall(tmp, members)
                whl.unlink(missing_ok=True)
            shutil.rmtree(base, ignore_errors=True)
            tmp.replace(base)
        if str(base) not in sys.path:
            sys.path.insert(0, str(base))
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(str(base / "ctranslate2"))
            except Exception:
                pass
        import ctranslate2  # noqa: PLC0415
        import tokenizers  # noqa: PLC0415

        log.info("Whisper : moteur CTranslate2 %s chargé depuis le cache", getattr(ctranslate2, "__version__", "?"))
        ENGINE_ERROR = ""
        return ctranslate2, tokenizers
    except Exception as exc:
        ENGINE_ERROR = _engine_error(exc)
        log.warning("Whisper : moteur indisponible", exc_info=True)
        return None


ENGINE_ERROR = ""


def diagnose(root: Path | None = None, load: bool = True) -> dict[str, Any]:
    """Self-check of the local speech engine (diagnostic / « Écoute » panel): ``engine`` True when
    CTranslate2 + tokenizers import (``load``: downloading the wheels if needed), ``error`` = why not,
    ``version``, ``model`` = the model files are present. Never raises."""
    out: dict[str, Any] = {"engine": False, "error": "", "version": "", "model": False}
    try:
        eng = import_engine(root) if load else None
        if eng is not None:
            out["engine"] = True
            out["version"] = str(getattr(eng[0], "__version__", "") or "")
        out["error"] = ENGINE_ERROR
        out["model"] = model_ready(DEFAULT_MODEL, root)
    except Exception as exc:
        out["error"] = _engine_error(exc)
    return out


# ------------------------------------------------------------------ transcriber
class WhisperSTT:
    """Lazy local Whisper. ``status`` (French) tells the UI where it is (download %, ready, error).
    ``backend`` may be injected for the tests: ``backend(pcm, prompt) -> str``."""

    def __init__(self, name: str = DEFAULT_MODEL, *, root: Path | None = None,
                 backend: Callable[[bytes, str], str] | None = None, model_dir: Path | None = None,
                 threads: int | None = None) -> None:
        self.name = name
        self._root = root
        self._model_dir = model_dir           # tools/stt_bench.py: any CTranslate2 Whisper folder
        self._threads = threads
        self.opts: dict[str, Any] = dict(DECODE)
        self._backend = backend
        self._model: Any = None
        self._tok: Any = None
        self._ids: dict[str, int] = {}
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._loading = False
        self._failed_at = -1e9
        self.status = "Whisper pas encore chargé."
        self.names: list[str] = []            # champions of the current game (prompt bias)
        self.last_ms = 0.0
        self.calls = 0

    def set_names(self, names: Iterable[str]) -> None:
        self.names = [str(n) for n in names if n][:12]

    def prompt(self) -> str:
        return build_prompt(self.names)

    def ready(self) -> bool:
        return self._backend is not None or self._model is not None

    def available_soon(self) -> bool:
        """Not given up: loaded, loading, or never tried / retry delay over."""
        return self.ready() or self._loading or time.monotonic() - self._failed_at > 600

    def load(self) -> bool:
        """Download (once) + load the model. Blocking (minutes the first time). Never raises."""
        if self.ready():
            return True
        with self._lock:
            if self.ready():
                return True
            if time.monotonic() - self._failed_at < 600:
                return False
            self._loading = True
            try:
                return self._load_locked()
            except Exception as exc:
                log.warning("Whisper : chargement échoué", exc_info=True)
                self.status = f"Whisper indisponible sur ce PC ({_engine_error(exc)}) : reconnaissance Vosk utilisée."
                self._failed_at = time.monotonic()
                return False
            finally:
                self._loading = False

    def _load_locked(self) -> bool:
        eng = import_engine(self._root)
        if eng is None:
            self.status = (f"Moteur Whisper indisponible ({ENGINE_ERROR}) : reconnaissance Vosk en attendant."
                           if ENGINE_ERROR else
                           "Moteur Whisper non téléchargé (Internet ?) : reconnaissance Vosk en attendant.")
            self._failed_at = time.monotonic()
            return False
        ct2, tokenizers = eng
        if self._model_dir is None and not model_ready(self.name, self._root):
            mb = MODELS[self.name][1]
            self.status = f"Téléchargement du modèle Whisper ({mb} Mo, une seule fois)…"
            log.info("Whisper : téléchargement du modèle %s", self.name)
            ok = download_model(self.name, self._root, progress=lambda p: setattr(
                self, "status", f"Téléchargement du modèle Whisper… {p} %"))
            if not ok:
                self.status = "Modèle Whisper non téléchargé (Internet ?) : reconnaissance Vosk en attendant."
                self._failed_at = time.monotonic()
                return False
        path = self._model_dir or model_path(self.name, self._root)
        t0 = time.monotonic()
        threads = self._threads or max(1, min(CPU_THREADS, (os.cpu_count() or 4)))
        self._model = ct2.models.Whisper(str(path), device="cpu", compute_type="int8", intra_threads=threads)
        self._tok = tokenizers.Tokenizer.from_file(str(path / "tokenizer.json"))
        for t in ("<|startoftranscript|>", "<|fr|>", "<|transcribe|>", "<|notimestamps|>", "<|startofprev|>",
                  "<|endoftext|>"):
            self._ids[t] = int(self._tok.token_to_id(t))
        self.status = f"Whisper {self.name} prêt (local, gratuit)."
        log.info("Whisper : modèle %s chargé en %.1f s (%d threads)", self.name, time.monotonic() - t0, threads)
        return True

    def transcribe(self, pcm: bytes, prompt: str | None = None, beam: int | None = None) -> str:
        """French text of one utterance (16 kHz mono int16). "" when not ready / nothing said. ``beam``:
        another beam size for this call (1 = the fast wake confirmation)."""
        if not pcm:
            return ""
        pcm = pcm[: int(MAX_UTTERANCE_S * SAMPLE_RATE) * 2]
        prompt = self.prompt() if prompt is None else prompt
        t0 = time.monotonic()
        try:
            with self._run_lock:                  # one decode at a time, whoever calls
                if self._backend is not None:
                    text = str(self._backend(pcm, prompt) or "")
                elif self._model is None:
                    return ""
                else:
                    text = self._run(pcm, prompt, beam)
        except Exception:
            log.warning("Whisper : transcription échouée", exc_info=True)
            return ""
        self.last_ms = (time.monotonic() - t0) * 1000
        self.calls += 1
        text = clean_text(text)
        log.info("Whisper : %.1f s d'audio en %.0f ms -> « %s »", len(pcm) / 2 / SAMPLE_RATE, self.last_ms, text[:80])
        return text

    def _run(self, pcm: bytes, prompt: str, beam: int | None = None) -> str:
        o = dict(self.opts, beam=beam) if beam else self.opts
        audio = preprocess(pcm_to_float(pcm), o)
        text = self._decode(audio, prompt if o.get("prompt", True) else "", o)
        if o.get("fix_terms"):
            text = fix_terms(text, self.names)
        return text

    def _decode(self, audio: Any, prompt: str, o: dict[str, Any] | None = None) -> str:
        import ctranslate2  # noqa: PLC0415

        o = self.opts if o is None else o
        n_mels = int(getattr(self._model, "n_mels", 80) or 80)
        feats = log_mel(audio, n_mels=n_mels, frames=input_frames(int(audio.size)))[None]
        ids = self._ids
        prev = self._tok.encode(" " + prompt.strip(), add_special_tokens=False).ids[-223:] if prompt else []
        seq = ([ids["<|startofprev|>"]] + prev if prev else []) + [
            ids["<|startoftranscript|>"], ids["<|fr|>"], ids["<|transcribe|>"], ids["<|notimestamps|>"]]
        sv = ctranslate2.StorageView.from_array(feats)
        if o.get("fallback"):
            sv = self._model.encode(sv)       # encoded once, decoded again only when the first try is bad
        temps = (0.0, 0.4) if o.get("fallback") else (0.0,)
        best = ""
        for t in temps:
            kw: dict[str, Any] = {"beam_size": int(o.get("beam", BEAM_SIZE))} if t == 0.0 else \
                {"beam_size": 1, "sampling_temperature": t, "sampling_topk": 0, "num_hypotheses": 1}
            res = self._model.generate(sv, [seq], max_length=160, suppress_blank=True,
                                       return_no_speech_prob=True, return_scores=True,
                                       repetition_penalty=float(o.get("repetition_penalty", 1.1)),
                                       no_repeat_ngram_size=int(o.get("no_repeat", 4)), **kw)
            r = res[0]
            if float(getattr(r, "no_speech_prob", 0.0) or 0.0) > float(o.get("no_speech", 0.7)):
                return ""
            out = [tk for tk in r.sequences_ids[0] if tk < ids["<|endoftext|>"]]
            text = self._tok.decode(out)
            score = float((r.scores or [0.0])[0])
            if t == 0.0:
                best = text
            if score >= -1.0 and _compression_ok(text):
                return text
            if t != 0.0 and score >= -1.0:
                return text
        return best


#: decoding / pre / post-processing options (tools/stt_bench.py measures each of them)
DECODE: dict[str, Any] = {"beam": BEAM_SIZE, "fallback": False, "no_speech": 0.7, "repetition_penalty": 1.1,
                          "no_repeat": 4, "prompt": True, "highpass": False, "norm": False, "gate": False,
                          "fix_terms": True}


def _compression_ok(text: str) -> bool:
    """Whisper's repetition-loop test: gzip ratio of the text <= 2.4."""
    import zlib  # noqa: PLC0415

    b = text.encode("utf-8")
    return len(b) < 24 or len(b) / max(1, len(zlib.compress(b))) <= 2.4


def preprocess(audio: Any, o: dict[str, Any]) -> Any:
    """Optional clean-up of float32 16 kHz audio: ``highpass`` (rumble / desk knocks < 100 Hz),
    ``gate`` (spectral noise gate: the stationary background — fans, game music bed — estimated on the
    quietest 10 % frames is subtracted), ``norm`` (speech brought to -20 dBFS RMS, peaks <= -1 dBFS)."""
    import numpy as np  # noqa: PLC0415

    x = np.asarray(audio, dtype=np.float32)
    if x.size < 400:
        return x
    if o.get("highpass"):
        f = np.fft.rfft(x)
        fr = np.fft.rfftfreq(x.size, 1.0 / SAMPLE_RATE)
        f *= 1.0 / np.sqrt(1.0 + (100.0 / np.maximum(fr, 1.0)) ** 4)     # 2nd-order Butterworth magnitude
        x = np.fft.irfft(f, n=x.size).astype(np.float32)
    if o.get("gate"):
        x = spectral_gate(x)
    if o.get("norm"):
        fr = x[: x.size // 320 * 320].reshape(-1, 320)
        rms = np.sqrt(np.mean(fr * fr, axis=1)) if fr.size else np.zeros(1)
        loud = np.sort(rms)[int(len(rms) * 0.7):] if len(rms) else rms
        level = float(np.mean(loud)) if loud.size else 0.0
        if level > 1e-4:
            g = min(30.0, 0.1 / level)
            peak = float(np.max(np.abs(x))) or 1.0
            g = min(g, 0.89 / peak) if g * peak > 0.89 else g
            x = (x * g).astype(np.float32)
    return x


def spectral_gate(x: Any, n_fft: int = 512, hop: int = 128, strength: float = 1.5, floor: float = 0.15) -> Any:
    """Stationary-noise spectral subtraction (overlap-add, Hann): noise profile = mean spectrum of the
    10 % quietest frames; each bin keeps max(floor, 1 - strength * noise / magnitude)."""
    import numpy as np  # noqa: PLC0415

    if x.size < n_fft * 4:
        return x
    win = np.hanning(n_fft).astype(np.float32)
    pad = np.concatenate([np.zeros(n_fft, np.float32), x, np.zeros(n_fft, np.float32)])
    idx = np.arange(n_fft)[None, :] + hop * np.arange((pad.size - n_fft) // hop + 1)[:, None]
    spec = np.fft.rfft(pad[idx] * win, axis=1)
    mag = np.abs(spec)
    energy = mag.sum(axis=1)
    quiet = mag[energy <= np.quantile(energy, 0.1)]
    noise = quiet.mean(axis=0) if quiet.size else np.zeros(mag.shape[1])
    gain = np.maximum(floor, 1.0 - strength * noise[None, :] / np.maximum(mag, 1e-9))
    frames = np.fft.irfft(spec * gain, n=n_fft, axis=1) * win
    out = np.zeros(pad.size, np.float32)
    norm = np.zeros(pad.size, np.float32)
    for k in range(frames.shape[0]):
        out[k * hop:k * hop + n_fft] += frames[k]
        norm[k * hop:k * hop + n_fft] += win * win
    out = out / np.maximum(norm, 1e-6)
    return out[n_fft:n_fft + x.size].astype(np.float32)


# ------------------------------------------------------------------ League names (post-processing)
#: summoner spells, keystones, objectives and the short names players give items (French client)
EXTRA_TERMS = (
    "Flash", "Saut éclair", "Embrasement", "Ignite", "Téléportation", "Fantôme", "Châtiment", "Soins",
    "Barrière", "Purge", "Fatigue", "Clarté", "Conquérant", "Électrocution", "Moisson noire", "Comète arcanique",
    "Poigne de l'immortel", "Invocation d'Aery", "Rythme mortel", "Premier coup", "Après-coup", "Gardien",
    "Frappe létale", "Baron", "Nashor", "Héraut", "Infernal", "Larves", "Trinité", "Zhonya",
    "Sterak", "Mercure", "Rabadon", "Shojin", "Jak'Sho", "Hullbreaker", "Botrk", "Ancien",
)
#: frequent French words a League name must never replace (« c'est » is not « Zed », « vie » not « Vi »)
COMMON_WORDS = frozenset("""
a ai as au aux avec ce ces cet cette c'est ça dans de des du elle elles en est et eu il ils je j'ai la le les
leur lui ma mais me mes moi mon ne ni nous on ou où par pas peu plus pour qu que quel quelle qui sa se ses si
son sur ta te tes toi ton tu un une vous vu y va vais vas faire fait fais faut bien bon tour tours vie mort
rien tout tous quoi comme quand comment combien contre avant après maintenant encore jamais toujours ulti
objet objets item items build lane mid top bot jungle jungler support ward wards gank ganks push back farm
dragon drake fight team roam reset level niveau coach hey hé où est sont avoir être prendre prends fait
power spike stuff full kill kills tank dps carry mana vision camp camps buff très trop carte grave quant
temps fois vois voir peux peut veux faut dois rentre reste rester joue jouer gagne perdu équipe partie
""".split())


_VOCAB: list[tuple[str, str]] | None = None


def phonetic(word: str) -> str:
    """A rough French phonetic key: accents off, letters only, k/c/q/qu -> k, ph -> f, y -> i,
    é/è/ai/ei -> e, silent h, doubled letters single."""
    import re  # noqa: PLC0415
    import unicodedata  # noqa: PLC0415

    w = unicodedata.normalize("NFD", str(word).lower())
    w = "".join(c for c in w if unicodedata.category(c) != "Mn")
    w = re.sub(r"[^a-z]", "", w)
    for a, b in (("ph", "f"), ("qu", "k"), ("ck", "k"), ("q", "k"), ("ch", "$"), ("c", "k"), ("$", "ch"),
                 ("y", "i"), ("ai", "e"), ("ei", "e"), ("au", "o"), ("eau", "o"), ("w", "v"), ("z", "s")):
        w = w.replace(a, b)
    w = re.sub(r"(?<![cs])h", "", w)
    return re.sub(r"(.)\1+", r"\1", w)


def lol_vocab() -> list[tuple[str, str]]:
    """``(phonetic key, canonical name)`` of every champion (French + English), item, summoner spell,
    keystone: the names a fuzzy match may restore. Read once from the bundled assets. Never raises."""
    global _VOCAB
    if _VOCAB is not None:
        return _VOCAB
    names: set[str] = set(EXTRA_TERMS)
    try:
        import json  # noqa: PLC0415

        from treeaicoach.paths import asset_path  # noqa: PLC0415

        idx = json.loads(asset_path("icons", "champions", "index.json").read_text(encoding="utf-8"))
        for c in idx.get("champions") or []:
            fr = str(c.get("name_fr") or c.get("name_en") or "")
            names.add(fr)
            en = str(c.get("name_en") or "")
            if en and phonetic(en) != phonetic(fr):
                names.add(en)
        items = json.loads(asset_path("items.json").read_text(encoding="utf-8")).get("items") or {}
        for it in items.values():
            n = str(it.get("n") or "")
            if n and len(n.split()) <= 4 and not it.get("k") == "consumable":
                names.add(n)
    except Exception:
        log.debug("League vocabulary unavailable", exc_info=True)
    _VOCAB = sorted({(phonetic(n), n) for n in names if len(phonetic(n)) >= 3})
    return _VOCAB


_WAKE_SPELLING = None


def fix_wake_word(text: str) -> str:
    """« Hé coque, … » / « coq » / « coke » at the START of a transcript -> « coach » (Whisper's frequent
    spelling of the wake word; nowhere else: « une coque » stays). Never raises."""
    global _WAKE_SPELLING
    try:
        if _WAKE_SPELLING is None:
            import re  # noqa: PLC0415

            _WAKE_SPELLING = re.compile(r"^(\s*(?:(?:h[ée]|hey|eh|et|ok)\b[\s,!.]*)?)(coques?|coqs?|cokes?|cocks?|cok)\b",
                                        re.IGNORECASE)
        return _WAKE_SPELLING.sub(lambda m: m.group(1) + ("Coach" if m.group(2)[0].isupper() else "coach"),
                                  str(text or ""), count=1)
    except Exception:
        return text


def fix_terms(text: str, game_names: Iterable[str] = (), threshold: float = 0.84) -> str:
    """Restore League names Whisper misspelled (« Kaïssa » -> « Kai'Sa », « Belvette » -> « Bel'Veth »):
    every 1-3 word window whose phonetic key is close to a name (difflib ratio >= ``threshold``; the
    champions of the current game at ``threshold - 0.06``) is replaced, longest windows first. Frequent
    French words are never replaced, names shorter than 4 letters need an exact phonetic match.
    Never raises (the text unchanged)."""
    import difflib  # noqa: PLC0415

    try:
        text = fix_wake_word(text)
        words = str(text or "").split()
        if not words:
            return text
        vocab = lol_vocab()
        game = {phonetic(n) for n in game_names if n}
        exact = {k for k, _n in vocab}
        by_len: dict[int, list[tuple[str, str]]] = {}
        for k, n in vocab:
            by_len.setdefault(len(k), []).append((k, n))
        out: list[str] = []
        i = 0
        while i < len(words):
            done = False
            for n_w in (3, 2, 1):
                if i + n_w > len(words):
                    continue
                win = words[i:i + n_w]
                core = [w.strip(",.!?;:«»\"()") for w in win]
                low = [c.lower().replace("’", "'") for c in core]
                if not all(core):
                    continue
                if all(w in COMMON_WORDS or len(w) <= 2 for w in low):
                    continue
                if n_w > 1 and (low[0] in COMMON_WORDS or low[-1] in COMMON_WORDS or len(low[0]) < 3
                                or len(low[-1]) < 3 or "'" in low[0][:3]):
                    continue                         # a name window never starts / ends on « le », « à », « t'ai »…
                if n_w > 1 and any(phonetic(c) in exact for c in core):
                    continue                         # « Blitzcrank à » : the name is already one word
                key = phonetic("".join(core))
                if len(key) < 3:
                    continue
                best, best_r = "", 0.0
                for ln in range(max(3, int(len(key) * 0.75)), int(len(key) * 1.3) + 2):
                    for k, name in by_len.get(ln, ()):
                        if len(k) < 5 and k != key and k not in game:
                            continue                 # short names (Zed, Vi, Pyke…): exact only, unless in game
                        sm = difflib.SequenceMatcher(None, key, k)
                        if sm.real_quick_ratio() < threshold - 0.06 or sm.quick_ratio() < threshold - 0.06:
                            continue
                        r = sm.ratio() + (0.06 if k in game else 0.0)
                        if r > best_r or (r == best_r and len(name) < len(best)):
                            best, best_r = name, r
                if best and best_r >= threshold:
                    if " ".join(core).lower() != best.lower():
                        trail = win[-1][len(win[-1].rstrip(",.!?;:»\")")):]
                        out.append(best + trail)
                        log.debug("Whisper : « %s » -> « %s » (%.2f)", " ".join(core), best, best_r)
                    else:
                        out.extend(win)
                    i += n_w
                    done = True
                    break
            if not done:
                out.append(words[i])
                i += 1
        return " ".join(out)
    except Exception:
        log.debug("fix_terms failed", exc_info=True)
        return text


#: Whisper's classic hallucinations on noise / silence (French YouTube credits)
_HALLUCINATIONS = ("sous-titres réalisés par", "sous-titrage", "merci d'avoir regardé", "amara.org",
                   "abonnez-vous", "sous-titres par")


def clean_text(text: str) -> str:
    t = " ".join(str(text or "").split()).strip()
    low = t.lower()
    if any(h in low for h in _HALLUCINATIONS):
        return ""
    return t


# ------------------------------------------------------------------ one worker for the whole app
#: job priorities (lower first): the question of a confirmed wake pre-empts everything waiting, then the
#: wake confirmations; FIFO within a priority
PRIO_QUESTION, PRIO_WAKE = 0, 1
#: an item that waited longer than this for the Whisper thread is dropped (the caller keeps Vosk's text /
#: the grammar's verdict): a late transcription only delays the answer (3.4.1: « 2 minutes » report)
QUEUE_MAX_AGE_S = 3.0
WAKE_MAX_AGE_S = 1.5             # a wake confirmation is only worth it right away
WAKE_TIMEOUT_S = 2.5             # the caller (wake engine) waits this long at most, then decides alone
QUESTION_TIMEOUT_S = 8.0         # the PC assistant waits this long at most for its question's text
#: the wake confirmation runs on this smaller model once it is downloaded (x3 faster, enough to read
#: « hé coach »); the question keeps :data:`DEFAULT_MODEL`
WAKE_MODEL = "base"


class Job:
    """One utterance waiting for Whisper. ``text`` is None until done (or dropped: stale / replaced).
    ``wait_ms`` / ``compute_ms``: queue wait and transcription time (latency log)."""

    __slots__ = ("pcm", "prompt", "key", "done", "t", "max_age", "text", "dropped", "ev", "beam", "prio",
                 "stt", "wait_ms", "compute_ms", "seq")

    def __init__(self, pcm: bytes, prompt: str | None, key: str, done: Callable[[str | None], None] | None,
                 t: float, max_age: float, prio: int = PRIO_QUESTION) -> None:
        self.pcm, self.prompt, self.key, self.done, self.t, self.max_age = pcm, prompt, key, done, t, max_age
        self.beam: int | None = None
        self.prio = int(prio)
        self.stt: Any = None                 # another transcriber for this job (the wake model)
        self.text: str | None = None
        self.dropped = False
        self.wait_ms = 0.0
        self.compute_ms = 0.0
        self.seq = 0
        self.ev = threading.Event()


def _below_normal_priority() -> None:
    """The Whisper thread yields to the game (Windows: THREAD_PRIORITY_BELOW_NORMAL). Never raises."""
    if sys.platform != "win32":
        return
    try:
        import ctypes  # noqa: PLC0415

        k = ctypes.windll.kernel32  # type: ignore[attr-defined]
        k.SetThreadPriority(k.GetCurrentThread(), -1)
    except Exception:
        log.debug("thread priority not set", exc_info=True)


class WhisperWorker:
    """ONE thread runs every Whisper transcription of the app (PC mic, each Discord speaker, the wake
    confirmations): a priority queue (the question of a confirmed wake first, then wake confirmations),
    at most one transcription at a time (the CPU is the game's; the thread runs below normal priority),
    stale items dropped (waited longer than their ``max_age``, :data:`QUEUE_MAX_AGE_S` by default: the
    player moved on), a newer item for the same ``key`` (speaker) replaces the waiting one.
    ``done(text | None)`` runs on the worker thread (None = dropped / failed: the caller keeps Vosk's text).
    ``threaded=False`` (tests): :meth:`submit` transcribes at once in the caller's thread; ``autostart=False``
    (simulation tests): jobs wait until :meth:`step` is called."""

    MAX_WAITING = 4

    def __init__(self, stt: Any = None, *, threaded: bool = True, clock: Callable[[], float] = time.monotonic,
                 autostart: bool = True) -> None:
        self._stt_obj = stt
        self._threaded = threaded
        self._autostart = autostart
        self._clock = clock
        self._jobs: list[Job] = []
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None
        self._seq = 0
        self.busy = False
        self.done_n = 0
        self.dropped = 0
        self.last_wait_ms = 0.0
        self.last_compute_ms = 0.0

    def stt(self) -> Any:
        return self._stt_obj if self._stt_obj is not None else transcriber()

    def pending(self) -> int:
        with self._cv:
            return len(self._jobs) + (1 if self.busy else 0)

    def submit(self, pcm: bytes, *, key: str = "", done: Callable[[str | None], None] | None = None,
               prompt: str | None = None, max_age: float = QUEUE_MAX_AGE_S, prio: int = PRIO_QUESTION,
               beam: int | None = None, stt: Any = None) -> Job:
        job = Job(pcm, prompt, key, done, self._clock(), max_age, prio)
        job.beam, job.stt = beam, stt
        return self._submit_job(job)

    def _submit_job(self, job: Job) -> Job:
        key = job.key
        if not self._threaded:
            self._execute(job)
            return job
        drop: list[Job] = []
        with self._cv:
            self._seq += 1
            job.seq = self._seq
            if key:
                drop = [j for j in self._jobs if j.key == key]
                self._jobs = [j for j in self._jobs if j.key != key]
            self._jobs.append(job)
            self._jobs.sort(key=lambda j: (j.prio, j.seq))
            while len(self._jobs) > self.MAX_WAITING:
                drop.append(self._jobs.pop())             # the least urgent, newest
            self._cv.notify()
            if self._autostart and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(target=self._run, name="TreeAI-whisper", daemon=True)
                self._thread.start()
        for j in drop:
            self._finish(j, None, dropped=True)
        return job

    def transcribe_job(self, pcm: bytes, *, key: str = "", prompt: str | None = None,
                       timeout: float = QUESTION_TIMEOUT_S, beam: int | None = None, prio: int = PRIO_QUESTION,
                       max_age: float = QUEUE_MAX_AGE_S, stt: Any = None) -> Job:
        """Blocking helper (the PC assistant's own thread): queued like the others, waits for its turn
        (``timeout`` at most: then the job is abandoned, ``text`` None)."""
        job = Job(pcm, prompt, key, None, self._clock(), max_age, prio)
        job.beam, job.stt = beam, stt
        job = self._submit_job(job)
        if not job.ev.wait(timeout):
            with self._cv:
                if job in self._jobs:                     # not started: never run it for nobody
                    self._jobs.remove(job)
        return job

    def transcribe(self, pcm: bytes, *, key: str = "", prompt: str | None = None,
                   timeout: float = QUESTION_TIMEOUT_S, beam: int | None = None, prio: int = PRIO_QUESTION,
                   max_age: float = QUEUE_MAX_AGE_S) -> str:
        return self.transcribe_job(pcm, key=key, prompt=prompt, timeout=timeout, beam=beam, prio=prio,
                                   max_age=max_age).text or ""

    def step(self, wait: float = 0.0) -> bool:
        """Run (or drop) the most urgent waiting job. False when none waited."""
        with self._cv:
            if not self._jobs and wait > 0:
                self._cv.wait(wait)
            if not self._jobs:
                return False
            job = self._jobs.pop(0)
            self.busy = True
        try:
            age = self._clock() - job.t
            if age > job.max_age:
                log.info("Whisper : %s abandonnée (attendait depuis %.1f s)",
                         "confirmation" if job.prio == PRIO_WAKE else "phrase", age)
                job.wait_ms = self.last_wait_ms = age * 1000
                job.compute_ms = self.last_compute_ms = 0.0
                self._finish(job, None, dropped=True)
            else:
                self._execute(job)
        finally:
            with self._cv:
                self.busy = False
        return True

    def _run(self) -> None:
        _below_normal_priority()
        while True:
            if not self.step(60.0):
                with self._cv:
                    if not self._jobs:
                        self._thread = None
                        return

    def _execute(self, job: Job) -> None:
        t0 = self._clock()
        job.wait_ms = self.last_wait_ms = (t0 - job.t) * 1000
        try:
            stt = job.stt if job.stt is not None else self.stt()
            text = stt.transcribe(job.pcm, job.prompt, beam=job.beam) if job.beam else stt.transcribe(job.pcm, job.prompt)
        except Exception:
            log.warning("Whisper : transcription échouée", exc_info=True)
            text = None
        job.compute_ms = self.last_compute_ms = (self._clock() - t0) * 1000
        self._finish(job, text)

    def _finish(self, job: Job, text: str | None, dropped: bool = False) -> None:
        job.text, job.dropped = text, dropped
        if dropped:
            self.dropped += 1
        else:
            self.done_n += 1
        job.ev.set()
        if job.done is not None:
            try:
                job.done(text)
            except Exception:
                log.debug("Whisper job callback failed", exc_info=True)


_SINGLETON: WhisperSTT | None = None
_WAKE: WhisperSTT | None = None
_WORKER: WhisperWorker | None = None
_SINGLETON_LOCK = threading.Lock()
_wake_loading = False


def transcriber() -> WhisperSTT:
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = WhisperSTT()
        return _SINGLETON


def is_app_transcriber(stt: Any) -> bool:
    return stt is not None and stt is _SINGLETON


def wake_transcriber() -> WhisperSTT | None:
    """The wake-confirmation model (:data:`WAKE_MODEL`, ~150 MB once) when loaded; its download / load
    starts in the background the first time (once the main model works). None meanwhile: the main model
    confirms. Never raises."""
    global _WAKE, _wake_loading
    try:
        main = _SINGLETON
        if WAKE_MODEL == getattr(main, "name", WAKE_MODEL):
            return None
        with _SINGLETON_LOCK:
            if _WAKE is None:
                _WAKE = WhisperSTT(WAKE_MODEL)
            w = _WAKE
            if w.ready():
                w.names = list(getattr(main, "names", []) or [])
                return w
            if _wake_loading or main is None or not main.ready() or not w.available_soon():
                return None
            _wake_loading = True

        def _go() -> None:
            global _wake_loading
            try:
                w.load()
            finally:
                _wake_loading = False
        threading.Thread(target=_go, name="TreeAI-whisper-wake-load", daemon=True).start()
    except Exception:
        log.debug("wake transcriber unavailable", exc_info=True)
    return None


def worker() -> WhisperWorker:
    """The app's single Whisper thread (PC mic + every Discord speaker + the wake confirmations)."""
    global _WORKER
    with _SINGLETON_LOCK:
        if _WORKER is None:
            _WORKER = WhisperWorker()
        return _WORKER


def wav_bytes(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    """A WAV file of 16-bit mono PCM (debug dumps / tests)."""
    import wave  # noqa: PLC0415

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()
