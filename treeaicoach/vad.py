"""Voice activity detection: Silero VAD v5 (MIT, ``assets/voice/silero_vad.onnx``, 2.3 MB, run with the
onnxruntime already shipped) cuts a 16 kHz mono int16 stream into utterances — the PC mic and each Discord
speaker get their own :class:`Segmenter`. When the model cannot load, an energy gate (adaptive noise
floor) takes over. Never raises."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME = 512                 # Silero v5 window at 16 kHz (32 ms)
CONTEXT = 64
START_P = 0.5
END_P = 0.35
MIN_SPEECH_S = 0.25
END_SILENCE_S = 1.0     # a short breath inside a sentence must not cut it
PRE_ROLL_S = 0.3
MAX_UTTERANCE_S = 15.0

_SESSION: Any = None
_SESSION_LOCK = threading.Lock()
_SESSION_FAILED = False


def _session() -> Any:
    global _SESSION, _SESSION_FAILED
    with _SESSION_LOCK:
        if _SESSION is None and not _SESSION_FAILED:
            try:
                import onnxruntime as ort  # noqa: PLC0415

                from treeaicoach.paths import asset_path  # noqa: PLC0415

                so = ort.SessionOptions()
                so.intra_op_num_threads = 1
                so.inter_op_num_threads = 1
                _SESSION = ort.InferenceSession(str(asset_path("voice", "silero_vad.onnx")), so,
                                                providers=["CPUExecutionProvider"])
                log.info("VAD : Silero chargé")
            except Exception:
                _SESSION_FAILED = True
                log.warning("VAD : Silero indisponible, détection par énergie", exc_info=True)
        return _SESSION


class SileroVAD:
    """Speech probability of each 512-sample frame (state kept between calls)."""

    def __init__(self, session: Any = None) -> None:
        import numpy as np  # noqa: PLC0415

        self._np = np
        self.sess = session if session is not None else _session()
        self.reset()

    @property
    def ok(self) -> bool:
        return self.sess is not None

    def reset(self) -> None:
        np = self._np
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._ctx = np.zeros((1, CONTEXT), dtype=np.float32)

    def prob(self, frame: Any) -> float:
        np = self._np
        x = np.asarray(frame, dtype=np.float32).reshape(1, -1)
        inp = np.concatenate([self._ctx, x], axis=1)
        out, self._state = self.sess.run(None, {"input": inp, "state": self._state,
                                                "sr": np.array(SAMPLE_RATE, dtype=np.int64)})
        self._ctx = inp[:, -CONTEXT:]
        return float(out[0][0])


class EnergyVAD:
    """Fallback: speech = frame RMS well above the tracked noise floor (and above digital silence)."""

    ok = True

    def __init__(self) -> None:
        self.floor: float | None = None

    def reset(self) -> None:
        pass

    def prob(self, frame: Any) -> float:
        import numpy as np  # noqa: PLC0415

        r = float(np.sqrt(np.mean(np.square(np.asarray(frame, dtype=np.float32) * 32768.0))))
        self.floor = r if self.floor is None else min(r, self.floor * 1.01 + 0.5)
        return 1.0 if r > max(150.0, 3.5 * self.floor) else 0.0


class Segmenter:
    """Feed 16 kHz mono int16 blocks of any size; :meth:`feed` returns the utterances that ENDED
    (int16 bytes, pre-roll included, <= :data:`MAX_UTTERANCE_S`). ``in_speech`` tells if one is open."""

    def __init__(self, vad: Any = None, *, end_silence_s: float = END_SILENCE_S,
                 min_speech_s: float = MIN_SPEECH_S, max_s: float = MAX_UTTERANCE_S) -> None:
        if vad is None:
            sv = SileroVAD()
            vad = sv if sv.ok else EnergyVAD()
        self.vad = vad
        self.kind = "silero" if isinstance(vad, SileroVAD) else type(vad).__name__
        self.end_frames = max(1, int(end_silence_s * SAMPLE_RATE / FRAME))
        self.min_frames = max(1, int(min_speech_s * SAMPLE_RATE / FRAME))
        self.max_frames = int(max_s * SAMPLE_RATE / FRAME)
        self.pre_frames = int(PRE_ROLL_S * SAMPLE_RATE / FRAME)
        self._buf = b""
        self._pre: list[bytes] = []
        self._cur: list[bytes] = []
        self._speech = 0
        self._silence = 0
        self.in_speech = False
        self.last_prob = 0.0
        self.on_start: Callable[[], None] | None = None

    def reset(self) -> None:
        self._buf, self._pre, self._cur = b"", [], []
        self._speech = self._silence = 0
        self.in_speech = False
        try:
            self.vad.reset()
        except Exception:
            pass

    def feed(self, pcm: bytes) -> list[bytes]:
        out: list[bytes] = []
        try:
            import numpy as np  # noqa: PLC0415

            self._buf += pcm
            n = FRAME * 2
            while len(self._buf) >= n:
                fb, self._buf = self._buf[:n], self._buf[n:]
                p = self.vad.prob(np.frombuffer(fb, dtype="<i2").astype(np.float32) / 32768.0)
                self.last_prob = p
                u = self._step(fb, p)
                if u:
                    out.append(u)
        except Exception:
            log.debug("VAD feed failed", exc_info=True)
        return out

    def _step(self, fb: bytes, p: float) -> bytes | None:
        if not self.in_speech:
            self._pre.append(fb)
            if len(self._pre) > self.pre_frames + self.min_frames:
                self._pre.pop(0)
            self._speech = self._speech + 1 if p >= START_P else 0
            if self._speech >= 2:                      # 64 ms of speech: an utterance opens
                self.in_speech, self._cur, self._pre = True, list(self._pre), []
                self._silence = 0
                if self.on_start is not None:
                    try:
                        self.on_start()
                    except Exception:
                        pass
            return None
        self._cur.append(fb)
        self._silence = self._silence + 1 if p < END_P else 0
        if self._silence >= self.end_frames or len(self._cur) >= self.max_frames:
            return self._close()
        return None

    def _close(self) -> bytes | None:
        cur = self._cur[: max(0, len(self._cur) - max(0, self._silence - 3))]   # keep ~0.1 s of tail
        voiced = len(cur) - self.pre_frames
        self._cur, self.in_speech, self._speech, self._silence = [], False, 0, 0
        return b"".join(cur) if voiced >= self.min_frames else None

    def flush(self) -> bytes | None:
        """The stream stopped (Discord speaker went quiet): close an open utterance."""
        if not self.in_speech:
            return None
        return self._close()

    def open_audio(self) -> bytes:
        """The utterance in progress so far (pre-roll included), b"" when none."""
        return b"".join(self._cur) if self.in_speech else b""


def speech_only(pcm: bytes, vad: Any = None, gap_s: float = 0.2) -> bytes:
    """The speech of ``pcm`` (16 kHz mono int16): every VAD utterance (pre-roll included) joined by
    ``gap_s`` of silence; long silences, comfort noise and the stream's empty tail are dropped, so Whisper
    reads only words (and less audio: faster). ``pcm`` itself when the VAD finds nothing. Never raises."""
    try:
        seg = Segmenter(vad, end_silence_s=0.5)
        parts = seg.feed(pcm)
        tail = seg.flush()
        if tail:
            parts.append(tail)
        if not parts:
            return pcm
        gap = b"\0\0" * int(gap_s * SAMPLE_RATE)
        return gap.join(parts)
    except Exception:
        log.debug("speech_only failed", exc_info=True)
        return pcm


def last_speech_start(blocks: list[bytes], vad: Any = None) -> int:
    """Index of the block where the LAST utterance of ``blocks`` starts (the « Hey coach » just heard,
    not what was said before it), minus one block of pre-roll; 0 when unknown. Never raises."""
    try:
        seg = Segmenter(vad, end_silence_s=0.4)
        start, was = 0, False
        for i, b in enumerate(blocks):
            seg.feed(b)
            if seg.in_speech and not was:
                start = max(0, i - 1)
            was = seg.in_speech
        return start
    except Exception:
        return 0
