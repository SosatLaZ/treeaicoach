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
CPU_THREADS = 4

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
    """The Whisper ``initial_prompt``: League vocabulary + the game's champion names (deduplicated)."""
    seen: list[str] = []
    for n in names:
        n = str(n or "").strip()
        if n and n not in seen and n not in vocab:
            seen.append(n)
    txt = vocab + (", " + ", ".join(seen) if seen else "") + "."
    return txt[:max_chars]


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


def import_engine(root: Path | None = None, opener: Callable[[str], Any] | None = None) -> tuple[Any, Any] | None:
    """``(ctranslate2, tokenizers)``: installed, else the pinned Windows wheels downloaded once. None on failure."""
    _stub_missing(("yaml", "tqdm"))
    try:
        import ctranslate2  # noqa: PLC0415
        import tokenizers  # noqa: PLC0415

        return ctranslate2, tokenizers
    except Exception:
        pass
    if sys.platform != "win32" and opener is None:
        return None
    try:
        base = (root or voice_cache()) / ENGINE_DIR_NAME
        if not all((base / pkg / "__init__.py").exists() for pkg, _u, _s in WHEELS):
            tmp = base.with_name(base.name + ".part")
            shutil.rmtree(tmp, ignore_errors=True)
            for pkg, url, sha in WHEELS:
                whl = tmp.parent / url.rsplit("/", 1)[-1]
                if not _fetch(url, sha, whl, opener):
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
        return ctranslate2, tokenizers
    except Exception as exc:
        global ENGINE_ERROR
        ENGINE_ERROR = f"{type(exc).__name__}"
        log.warning("Whisper : moteur indisponible", exc_info=True)
        return None


ENGINE_ERROR = ""


# ------------------------------------------------------------------ transcriber
class WhisperSTT:
    """Lazy local Whisper. ``status`` (French) tells the UI where it is (download %, ready, error).
    ``backend`` may be injected for the tests: ``backend(pcm, prompt) -> str``."""

    def __init__(self, name: str = DEFAULT_MODEL, *, root: Path | None = None,
                 backend: Callable[[bytes, str], str] | None = None) -> None:
        self.name = name
        self._root = root
        self._backend = backend
        self._model: Any = None
        self._tok: Any = None
        self._ids: dict[str, int] = {}
        self._lock = threading.Lock()
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
            except Exception:
                log.warning("Whisper : chargement échoué", exc_info=True)
                self.status = "Whisper indisponible sur ce PC : reconnaissance Vosk utilisée."
                self._failed_at = time.monotonic()
                return False
            finally:
                self._loading = False

    def _load_locked(self) -> bool:
        eng = import_engine(self._root)
        if eng is None:
            self.status = "Moteur Whisper non téléchargé (Internet ?) : reconnaissance Vosk en attendant."
            self._failed_at = time.monotonic()
            return False
        ct2, tokenizers = eng
        if not model_ready(self.name, self._root):
            mb = MODELS[self.name][1]
            self.status = f"Téléchargement du modèle Whisper ({mb} Mo, une seule fois)…"
            log.info("Whisper : téléchargement du modèle %s", self.name)
            ok = download_model(self.name, self._root, progress=lambda p: setattr(
                self, "status", f"Téléchargement du modèle Whisper… {p} %"))
            if not ok:
                self.status = "Modèle Whisper non téléchargé (Internet ?) : reconnaissance Vosk en attendant."
                self._failed_at = time.monotonic()
                return False
        path = model_path(self.name, self._root)
        t0 = time.monotonic()
        threads = max(1, min(CPU_THREADS, (os.cpu_count() or 4)))
        self._model = ct2.models.Whisper(str(path), device="cpu", compute_type="int8", intra_threads=threads)
        self._tok = tokenizers.Tokenizer.from_file(str(path / "tokenizer.json"))
        for t in ("<|startoftranscript|>", "<|fr|>", "<|transcribe|>", "<|notimestamps|>", "<|startofprev|>",
                  "<|endoftext|>"):
            self._ids[t] = int(self._tok.token_to_id(t))
        self.status = f"Whisper {self.name} prêt (local, gratuit)."
        log.info("Whisper : modèle %s chargé en %.1f s (%d threads)", self.name, time.monotonic() - t0, threads)
        return True

    def transcribe(self, pcm: bytes, prompt: str | None = None) -> str:
        """French text of one utterance (16 kHz mono int16). "" when not ready / nothing said."""
        if not pcm:
            return ""
        pcm = pcm[: int(MAX_UTTERANCE_S * SAMPLE_RATE) * 2]
        prompt = self.prompt() if prompt is None else prompt
        t0 = time.monotonic()
        try:
            if self._backend is not None:
                text = str(self._backend(pcm, prompt) or "")
            elif self._model is None:
                return ""
            else:
                text = self._run(pcm, prompt)
        except Exception:
            log.warning("Whisper : transcription échouée", exc_info=True)
            return ""
        self.last_ms = (time.monotonic() - t0) * 1000
        self.calls += 1
        text = clean_text(text)
        log.info("Whisper : %.1f s d'audio en %.0f ms -> « %s »", len(pcm) / 2 / SAMPLE_RATE, self.last_ms, text[:80])
        return text

    def _run(self, pcm: bytes, prompt: str) -> str:
        import ctranslate2  # noqa: PLC0415

        n_mels = int(getattr(self._model, "n_mels", 80) or 80)
        feats = log_mel(pcm_to_float(pcm), n_mels=n_mels)[None]
        ids = self._ids
        prev = self._tok.encode(" " + prompt.strip(), add_special_tokens=False).ids[-223:] if prompt else []
        seq = ([ids["<|startofprev|>"]] + prev if prev else []) + [
            ids["<|startoftranscript|>"], ids["<|fr|>"], ids["<|transcribe|>"], ids["<|notimestamps|>"]]
        res = self._model.generate(ctranslate2.StorageView.from_array(feats), [seq], beam_size=3,
                                   max_length=160, suppress_blank=True, return_no_speech_prob=True,
                                   repetition_penalty=1.1, no_repeat_ngram_size=4)
        r = res[0]
        if float(getattr(r, "no_speech_prob", 0.0) or 0.0) > 0.7:
            return ""
        out = [t for t in r.sequences_ids[0] if t < ids["<|endoftext|>"]]
        return self._tok.decode(out)


#: Whisper's classic hallucinations on noise / silence (French YouTube credits)
_HALLUCINATIONS = ("sous-titres réalisés par", "sous-titrage", "merci d'avoir regardé", "amara.org",
                   "abonnez-vous", "sous-titres par")


def clean_text(text: str) -> str:
    t = " ".join(str(text or "").split()).strip()
    low = t.lower()
    if any(h in low for h in _HALLUCINATIONS):
        return ""
    return t


_SINGLETON: WhisperSTT | None = None
_SINGLETON_LOCK = threading.Lock()


def transcriber() -> WhisperSTT:
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = WhisperSTT()
        return _SINGLETON


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
