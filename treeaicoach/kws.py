"""Wake-word spotter « Hey coach » (Siri-like): a small neural keyword spotter, never a speech recognizer.

Every 80 ms of 16 kHz mono audio:

1. log-mel spectrogram (``melspectrogram.onnx``, 32 bands, 10 ms hop) and
2. a 96-d speech embedding of the last 0.76 s (``embedding_model.onnx``: Google's ``speech_embedding``,
   Apache-2.0, as re-implemented by openWakeWord 0.5.1, Apache-2.0) — both downloaded ONCE on first use
   into ``paths.cache_dir()/voice/kws`` and checked by SHA-256 (:data:`FEATURE_MODELS`, 2.4 MB);
3. our head (``assets/voice/kws_hey_coach.onnx``, trained by ``tools/kws_train.py`` on synthetic French
   voices only) scores the last 16 embeddings (1.28 s): P(« Hey coach » / « Hé coach » / « Et coach » /
   « Hey Tree » just ended).

A wake = the score at or above the path's threshold on 2 consecutive frames (:class:`Spotter`). Silence
costs nothing: while the last 0.8 s stays below :data:`GATE_RMS` the embedding is not computed (the cached
« quiet » embedding is used, exactly as during training). One :class:`KwsModel` (ONNX sessions, 1 thread)
is shared by every stream (PC mic, each Discord member). Nothing here raises into the caller."""

from __future__ import annotations

import collections
import hashlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHUNK = 1280                    # 80 ms: one embedding (8 mel frames of 10 ms)
MEL_CONTEXT = 480               # extra samples in front of each chunk (the mel window / openWakeWord's 160*3)
MEL_WINDOW = 76                 # mel frames per embedding
EMB_WINDOW = 16                 # embeddings seen by the head (1.28 s)
GATE_RMS = 40.0                 # int16 RMS: below this for a whole embedding window = silence (no compute)
CONFIRM_FRAMES = 2              # consecutive frames at/above the threshold
REFRACTORY_S = 1.5              # no second wake from the same « Hey coach »
#: per path thresholds (``tools/wake_bench.py`` picks them: false wakes ≤ 0.2 / h on the chatter set)
THRESHOLDS = {"pc": 0.5, "discord": 0.5}

_REL = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
#: name -> (url, sha256, bytes). Apache-2.0 (openWakeWord code; the embedding weights are Google's
#: speech_embedding TFHub module, Apache-2.0). openWakeWord's own WAKE-WORD models (CC BY-NC-SA) are NOT used.
FEATURE_MODELS: dict[str, tuple[str, str, int]] = {
    "melspectrogram.onnx": (_REL + "melspectrogram.onnx",
                            "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f", 1087958),
    "embedding_model.onnx": (_REL + "embedding_model.onnx",
                             "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f", 1326578),
}
HEAD_NAME = "kws_hey_coach.onnx"
DOWNLOAD_MB = 2.4


def model_dir() -> Path:
    from treeaicoach.paths import cache_dir  # noqa: PLC0415

    return cache_dir() / "voice" / "kws"


def head_path() -> Path:
    from treeaicoach.paths import asset_path  # noqa: PLC0415

    return Path(asset_path("voice", HEAD_NAME))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def features_ready(folder: Path | None = None) -> bool:
    folder = folder or model_dir()
    return all((folder / n).is_file() and (folder / n).stat().st_size == size
               for n, (_u, _s, size) in FEATURE_MODELS.items())


def download_features(folder: Path | None = None, opener: Callable[[str], Any] | None = None,
                      progress: Callable[[int], None] | None = None) -> bool:
    """Download the 2 feature models (2.4 MB) once, SHA-256 checked, atomic rename. Never raises."""
    folder = folder or model_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if opener is None:
            import urllib.request  # noqa: PLC0415

            opener = lambda u: urllib.request.urlopen(u, timeout=30)  # noqa: E731
        done = 0
        for name, (url, sha, size) in FEATURE_MODELS.items():
            dst = folder / name
            if dst.is_file() and dst.stat().st_size == size and _sha256(dst) == sha:
                done += size
                continue
            part = folder / (name + ".part")
            with opener(url) as resp, open(part, "wb") as fh:
                while True:
                    b = resp.read(1 << 16)
                    if not b:
                        break
                    fh.write(b)
                    done += len(b)
                    if progress is not None:
                        progress(min(100, int(100 * done / sum(s for _u, _h, s in FEATURE_MODELS.values()))))
            got = _sha256(part)
            if got != sha:
                part.unlink(missing_ok=True)
                raise ValueError(f"{name}: SHA-256 {got[:12]}… != {sha[:12]}…")
            part.replace(dst)
        return True
    except Exception:
        log.warning("KWS : téléchargement des modèles impossible", exc_info=True)
        return False


class KwsModel:
    """The 3 ONNX sessions (1 thread each), shared by every :class:`Spotter`. ``mel`` / ``embed`` / ``score``
    take numpy arrays; :meth:`clip_embeddings` is the batch path used by training (same numbers as streaming)."""

    def __init__(self, folder: Path | None = None, head: Path | None = None) -> None:
        import numpy as np  # noqa: PLC0415
        import onnxruntime as ort  # noqa: PLC0415

        self.np = np
        folder = folder or model_dir()
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        so.log_severity_level = 3
        prov = ["CPUExecutionProvider"]
        self._mel = ort.InferenceSession(str(folder / "melspectrogram.onnx"), so, providers=prov)
        self._emb = ort.InferenceSession(str(folder / "embedding_model.onnx"), so, providers=prov)
        self._emb_in = self._emb.get_inputs()[0].name
        self._head = None
        hp = head if head is not None else head_path()
        if hp is not None and Path(hp).is_file():
            self._head = ort.InferenceSession(str(hp), so, providers=prov)
            self._head_in = self._head.get_inputs()[0].name
        self.lock = threading.Lock()
        self.quiet = self.embed(self.mel(np.zeros(CHUNK * 10 + MEL_CONTEXT, np.float32))[-MEL_WINDOW:][None])[0]

    # -- building blocks
    def mel(self, samples: Any) -> Any:
        """int16-valued float samples -> (frames, 32) (openWakeWord's scaling: x / 10 + 2)."""
        x = self.np.asarray(samples, dtype=self.np.float32)[None]
        return self.np.squeeze(self._mel.run(None, {"input": x})[0], axis=(0, 1)) / 10.0 + 2.0

    def embed(self, windows: Any) -> Any:
        """(n, 76, 32) -> (n, 96)."""
        w = self.np.asarray(windows, dtype=self.np.float32)[..., None]
        return self._emb.run(None, {self._emb_in: w})[0].reshape(len(w), -1)

    def score(self, feats: Any) -> Any:
        """(n, 16, 96) -> (n,) wake probabilities."""
        if self._head is None:
            return self.np.zeros(len(feats), self.np.float32)
        return self._head.run(None, {self._head_in: self.np.asarray(feats, self.np.float32)})[0].reshape(-1)

    def clip_embeddings(self, pcm: Any, gate: float = GATE_RMS) -> Any:
        """Embeddings of a whole int16 clip, one per 80 ms chunk, exactly as :class:`Spotter` computes them
        streaming (zeros in front, the quiet embedding where the gate is closed). (n_chunks, 96)."""
        np = self.np
        a = np.asarray(pcm, dtype=np.float32)
        a = np.concatenate([np.zeros(MEL_CONTEXT, np.float32), a[: a.size // CHUNK * CHUNK]])
        n = (a.size - MEL_CONTEXT) // CHUNK
        if n <= 0:
            return np.zeros((0, 96), np.float32)
        # the mel model clamps relative to the loudest bin of ITS input: computed per chunk, as streaming
        mel = np.concatenate([self.mel(a[i * CHUNK:i * CHUNK + CHUNK + MEL_CONTEXT]) for i in range(n)])
        rms = np.sqrt(np.mean(a[MEL_CONTEXT:].reshape(n, CHUNK) ** 2, axis=1))
        out = np.repeat(self.quiet[None], n, axis=0)
        mel_hist = np.concatenate([np.ones((MEL_WINDOW, 32), np.float32), mel])   # streaming starts on ones
        idx, wins = [], []
        for i in range(n):
            if rms[max(0, i - 9):i + 1].max() < gate:
                continue
            end = MEL_WINDOW + 8 * (i + 1)
            wins.append(mel_hist[end - MEL_WINDOW:end])
            idx.append(i)
        for k in range(0, len(wins), 512):
            out[idx[k:k + 512]] = self.embed(np.stack(wins[k:k + 512]))
        return out


class Spotter:
    """One audio stream (PC mic or one Discord member). :meth:`feed` takes int16 mono 16 kHz bytes of any
    length and returns True on a wake. ``scores`` keeps the last few seconds for the UI / logs."""

    def __init__(self, model: KwsModel, threshold: float = 0.5, confirm: int = CONFIRM_FRAMES,
                 clock: Callable[[], float] | None = None) -> None:
        np = model.np
        self.np = np
        self.model = model
        self.threshold = float(threshold)
        self.confirm = max(1, int(confirm))
        self._clock = clock
        self.scores: collections.deque[float] = collections.deque(maxlen=40)
        self.last_score = 0.0
        self.peak = 0.0                  # best score since the last wake / reset (logs: near misses)
        self.rms = 0.0
        self.frames = 0
        self.computed = 0                # embeddings really computed (CPU accounting)
        self.best2 = 0.0
        self.reset()

    def reset(self) -> None:
        np = self.np
        self._buf = bytearray()
        self._raw = np.zeros(MEL_CONTEXT, np.float32)
        self._mel = np.ones((MEL_WINDOW, 32), np.float32)
        self._emb = np.repeat(self.model.quiet[None], EMB_WINDOW, axis=0)
        self._rms: collections.deque[float] = collections.deque([0.0] * 10, maxlen=10)
        self._hot = 0
        self._cool = 0
        self.last_score = 0.0
        self.peak = 0.0
        self._prev = 0.0

    def feed(self, pcm: bytes) -> bool:
        woke = False
        try:
            self._buf += pcm
            n = CHUNK * 2
            while len(self._buf) >= n:
                chunk, self._buf = bytes(self._buf[:n]), self._buf[n:]
                woke = self._step(chunk) or woke
        except Exception:
            log.debug("KWS feed failed", exc_info=True)
        return woke

    def _step(self, chunk: bytes) -> bool:
        np = self.np
        m = self.model
        x = np.frombuffer(chunk, "<i2").astype(np.float32)
        self.frames += 1
        r = float(np.sqrt(np.mean(x * x)))
        self.rms = r
        self._rms.append(r)
        audio = np.concatenate([self._raw, x])
        self._raw = audio[-MEL_CONTEXT:]
        with m.lock:
            mel = m.mel(audio)
            self._mel = np.concatenate([self._mel, mel])[-MEL_WINDOW:]
            if max(self._rms) < GATE_RMS:
                e = m.quiet
            else:
                e = m.embed(self._mel[None])[0]
                self.computed += 1
            self._emb = np.concatenate([self._emb[1:], e[None]])
            s = float(m.score(self._emb[None])[0]) if self.computed else 0.0
        self.last_score = s
        self.peak = max(self.peak, s)
        self.best2 = max(self.best2, min(s, self._prev))    # best score held 2 frames (bench sweeps)
        self._prev = s
        self.scores.append(s)
        if self._cool > 0:
            self._cool -= 1
            self._hot = 0
            return False
        self._hot = self._hot + 1 if s >= self.threshold else 0
        if self._hot >= self.confirm:
            self._hot = 0
            self._cool = int(REFRACTORY_S * SAMPLE_RATE / CHUNK)
            return True
        return False


_MODEL: KwsModel | None = None
_MODEL_LOCK = threading.Lock()
_FAILED_AT = [-1e9]
STATUS = ["non chargé"]


def shared_model(download: bool = True, now: float | None = None) -> KwsModel | None:
    """The process-wide model: feature models downloaded if needed (once; a failure retries after 10 min),
    None when unavailable (the caller falls back to Vosk). Never raises."""
    import time  # noqa: PLC0415

    global _MODEL
    t = time.monotonic() if now is None else now
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        if t - _FAILED_AT[0] < 600.0:
            return None
        try:
            if not head_path().is_file():
                raise FileNotFoundError(HEAD_NAME)
            if not features_ready():
                if not download:
                    return None
                STATUS[0] = f"téléchargement du détecteur « Hey coach » (~{DOWNLOAD_MB:.1f} Mo)"
                if not download_features():
                    raise OSError("download failed")
            _MODEL = KwsModel()
            STATUS[0] = "prêt"
            log.info("KWS : détecteur « Hey coach » prêt")
            return _MODEL
        except Exception as exc:
            _FAILED_AT[0] = t
            STATUS[0] = f"indisponible ({type(exc).__name__})"
            log.warning("KWS : détecteur indisponible, repli sur Vosk", exc_info=True)
            return None
