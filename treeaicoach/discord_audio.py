"""Audio of the bot Discord: what the friends hear, made robust and at a sane level.

* :func:`parse_wav`: any WAV a voice engine writes (PCM 8 / 16 / 24 / 32 bit, float 32, the
  ``WAVE_FORMAT_EXTENSIBLE`` header Python 3.11's ``wave`` refuses) -> float samples. Never raises.
* :func:`speech_pcm` / :func:`chime_pcm`: 48 kHz stereo s16le PCM in whole 20 ms frames (what
  discord.py's ``PCMAudio`` sends), loudness-normalised: speech at :data:`TARGET_RMS_DB` (RMS of
  the voiced 20 ms blocks, a cheap stand-in for -20 LUFS), the chime :data:`CHIME_UNDER_DB` under it.
* :func:`apply_volume`: the "Volume du bot" slider (config ``discord_volume`` 0-100, default 40 =
  about 35 % of the 2.9.0 level) + a soft limiter (never clips). Applied at play time, so the clips
  are cached once at unity gain.
* :func:`is_audible`: the check that a clip really holds speech (2.9.0 played the chime alone when
  the synthesis came back empty, silently).
* :class:`BotVoice`: text -> (WAV, source) through a chain verified at each step: the natural voice
  (Edge, tts_neural) -> Windows OneCore (WinRT, in its own thread with the COM apartment set) ->
  Windows SAPI written to a WAV file (SpVoice + SpFileStream) -> the clips recorded at build time
  in ``assets/sounds/voice`` (Denise, ``tools/make_bot_voice_clips.py``). The source used is logged
  and shown on the launcher page ("Voix : naturelle / Windows / enregistrée").
"""

from __future__ import annotations

import logging
import os
import re
import struct
import sys
import tempfile
import threading
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

OUT_RATE = 48000
FRAME_BYTES = 3840               # 20 ms of 48 kHz stereo s16
TARGET_RMS_DB = -20.0            # speech loudness after normalisation (dBFS RMS of the voiced blocks)
CHIME_UNDER_DB = 6.0             # the chime sits this far under the speech
MIN_AUDIBLE_S = 0.15             # shorter than this: not a sentence
MIN_RMS_DB = -50.0               # quieter than this: silence (an empty / broken synthesis)
DEFAULT_VOLUME = 40
#: 2.9.0 played the natural voice unscaled (Denise "Bot gank": -20 dBFS RMS) and the chime at
#: -3 dBFS peak / -13 dBFS RMS (7 dB LOUDER than the words: "le volume est trop haut"). Now, at the
#: default volume 40, the speech plays at 35 % of that amplitude (-9.1 dB: -29 dBFS RMS) and the chime
#: 6 dB under it (-35 dBFS RMS, 22 dB under 2.9.0's). Under 40 the gain falls linearly to silence;
#: above, +0.2 dB per step up to +2.9 dB at 100 (-17 dBFS RMS, a bit louder than 2.9.0's voice).
DEFAULT_GAIN = 0.35
GAIN_DB_PER_STEP = 0.2
LIMIT_KNEE = 0.5                 # the soft limiter starts bending here (full scale = 1.0)
STEP_TIMEOUT_S = {"edge": 4.0, "onecore": 5.0, "sapi": 6.0}

#: source -> what the launcher shows after "Voix : "
SOURCE_LABELS = {"edge": "naturelle", "onecore": "Windows", "sapi": "Windows", "clip": "enregistrée",
                 "custom": "naturelle"}

#: the sentences recorded at build time (assets/sounds/voice/<clip_name>.wav)
RECORDED = ("Top gank", "Mid gank", "Bot gank", "Jungle gank",
            "Dragon dans 30 secondes", "Baron dans 30 secondes", "Larves dans 30 secondes",
            "Jungler ennemi vu top", "Jungler ennemi vu mid", "Jungler ennemi vu bot")


# ------------------------------------------------------------------------------ WAV in
def parse_wav(data: Any) -> tuple[Any, int] | None:
    """``(samples float32 (n, channels) in [-1, 1], rate)`` of a RIFF/WAVE byte string, None when it
    holds no readable audio. PCM 8/16/24/32, float 32/64 and WAVE_FORMAT_EXTENSIBLE. Never raises."""
    try:
        import numpy as np  # noqa: PLC0415

        b = bytes(data or b"")
        if len(b) < 44 or b[:4] != b"RIFF" or b[8:12] != b"WAVE":
            return None
        pos, fmt, raw = 12, None, None
        while pos + 8 <= len(b):
            cid, size = b[pos:pos + 4], struct.unpack("<I", b[pos + 4:pos + 8])[0]
            body = b[pos + 8:pos + 8 + size]
            if cid == b"fmt " and len(body) >= 16:
                tag, ch, rate, _br, _al, bits = struct.unpack("<HHIIHH", body[:16])
                if tag == 0xFFFE and len(body) >= 26:          # extensible: the real tag is the subformat
                    tag = struct.unpack("<H", body[24:26])[0]
                fmt = (tag, ch, rate, bits)
            elif cid == b"data":
                raw = body                                     # a data size past the end (streams): what is there
            if raw is not None and fmt is not None:
                break
            pos += 8 + size + (size & 1)
        if fmt is None or not raw:
            return None
        tag, ch, rate, bits = fmt
        if ch < 1 or ch > 8 or rate < 4000:
            return None
        width = bits // 8
        if width < 1:
            return None
        raw = raw[: len(raw) // (width * ch) * width * ch]
        if not raw:
            return None
        if tag == 1 and bits == 16:
            x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        elif tag == 1 and bits == 8:
            x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif tag == 1 and bits == 24:
            a = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
            v = a[:, 0] | (a[:, 1] << 8) | (a[:, 2] << 16)
            v = np.where(v >= 1 << 23, v - (1 << 24), v)
            x = v.astype(np.float32) / float(1 << 23)
        elif tag == 1 and bits == 32:
            x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / float(1 << 31)
        elif tag == 3 and bits in (32, 64):
            x = np.frombuffer(raw, dtype="<f4" if bits == 32 else "<f8").astype(np.float32)
        else:
            return None
        x = np.nan_to_num(x.reshape(-1, ch), nan=0.0, posinf=0.0, neginf=0.0)
        return np.clip(x, -1.0, 1.0), int(rate)
    except Exception:
        log.debug("parse_wav failed", exc_info=True)
        return None


def _to_48k_stereo(x: Any, rate: int) -> Any:
    import numpy as np  # noqa: PLC0415

    x = x.astype(np.float32)
    if x.shape[1] > 2:
        x = x[:, :2]
    if rate != OUT_RATE and x.shape[0] > 1:
        m = max(1, int(round(x.shape[0] * OUT_RATE / rate)))
        src = np.arange(x.shape[0], dtype=np.float64)
        dst = np.linspace(0.0, x.shape[0] - 1, m)
        x = np.stack([np.interp(dst, src, x[:, c]) for c in range(x.shape[1])], axis=1).astype(np.float32)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    return x


def voiced_rms_db(x: Any, rate: int = OUT_RATE) -> float:
    """RMS level (dBFS) of the voiced part: 20 ms blocks within 30 dB of the loudest block (speech
    pauses and the silent tail do not lower it). ``-inf`` for silence. Never raises."""
    try:
        import numpy as np  # noqa: PLC0415

        mono = np.asarray(x, dtype=np.float64)
        if mono.ndim == 2:
            mono = mono.mean(axis=1)
        n = max(1, int(rate * 0.02))
        k = len(mono) // n
        if k == 0:
            e = float(np.mean(mono ** 2)) if len(mono) else 0.0
            return 10.0 * np.log10(e) if e > 0 else float("-inf")
        blocks = (mono[: k * n].reshape(k, n) ** 2).mean(axis=1)
        top = float(blocks.max())
        if top <= 0:
            return float("-inf")
        keep = blocks[blocks >= top * 1e-3]
        return float(10.0 * np.log10(float(keep.mean())))
    except Exception:
        return float("-inf")


def _pcm_bytes(x: Any) -> bytes:
    import numpy as np  # noqa: PLC0415

    pcm = np.clip(np.round(x * 32767.0), -32768, 32767).astype("<i2").tobytes()
    return pcm + b"\x00" * ((-len(pcm)) % FRAME_BYTES)


def to_pcm48(data: Any, target_db: float | None = None) -> bytes:
    """A WAV -> 48 kHz stereo s16le PCM padded to 20 ms frames; with ``target_db`` normalised to that
    voiced RMS level (soft-limited). ``b""`` if unreadable."""
    try:
        import numpy as np  # noqa: PLC0415

        parsed = parse_wav(data)
        if parsed is None:
            return b""
        x = _to_48k_stereo(*parsed)
        if x.shape[0] == 0:
            return b""
        if target_db is not None:
            level = voiced_rms_db(x)
            if np.isfinite(level):
                x = soft_limit(x * (10.0 ** ((target_db - level) / 20.0)))
        return _pcm_bytes(x)
    except Exception:
        log.debug("to_pcm48 failed", exc_info=True)
        return b""


def speech_pcm(wav: Any) -> bytes:
    """Speech for the bot: 48 kHz stereo PCM at :data:`TARGET_RMS_DB` (unity volume)."""
    return to_pcm48(wav, TARGET_RMS_DB)


def chime_pcm(wav: Any) -> bytes:
    """The danger chime for the bot: :data:`CHIME_UNDER_DB` under the speech (unity volume)."""
    return to_pcm48(wav, TARGET_RMS_DB - CHIME_UNDER_DB)


# ------------------------------------------------------------------------------ level
def soft_limit(x: Any, knee: float = LIMIT_KNEE) -> Any:
    """Identity under ``knee``, then a tanh curve that never reaches full scale."""
    import numpy as np  # noqa: PLC0415

    a = np.abs(x)
    over = a > knee
    if not np.any(over):
        return x
    room = 1.0 - knee
    y = np.array(x, dtype=np.float32, copy=True)
    y[over] = np.sign(x[over]) * (knee + room * 0.98 * np.tanh((a[over] - knee) / room))
    return y


def volume_gain(volume: Any) -> float:
    """Amplitude factor of the slider: 0 -> 0, :data:`DEFAULT_VOLUME` -> :data:`DEFAULT_GAIN`
    (linear under it), then :data:`GAIN_DB_PER_STEP` dB per step (100 -> x1.39)."""
    try:
        v = max(0.0, min(100.0, float(volume)))
    except (TypeError, ValueError):
        v = float(DEFAULT_VOLUME)
    if v <= DEFAULT_VOLUME:
        return DEFAULT_GAIN * v / DEFAULT_VOLUME
    return DEFAULT_GAIN * 10.0 ** ((v - DEFAULT_VOLUME) * GAIN_DB_PER_STEP / 20.0)


def apply_volume(pcm: bytes, volume: Any) -> bytes:
    """PCM (s16le) scaled by the bot volume, soft-limited. ``b""`` stays ``b""``. Never raises."""
    try:
        if not pcm:
            return b""
        import numpy as np  # noqa: PLC0415

        g = volume_gain(volume)
        x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
        y = soft_limit(x * g)
        out = np.clip(np.round(y * 32767.0), -32768, 32767).astype("<i2").tobytes()
        return out + pcm[len(out):]
    except Exception:
        log.debug("apply_volume failed", exc_info=True)
        return pcm


def pcm_stats(pcm: bytes) -> dict:
    """``{seconds, rms_db (voiced), peak_db}`` of 48 kHz stereo s16le PCM."""
    try:
        import numpy as np  # noqa: PLC0415

        x = np.frombuffer(pcm[: len(pcm) // 4 * 4], dtype="<i2").astype(np.float64).reshape(-1, 2) / 32768.0
        peak = float(np.abs(x).max()) if x.size else 0.0
        # trailing frame padding is silence: count up to the last non-zero sample
        nz = np.nonzero(np.abs(x).max(axis=1) > 0)[0]
        secs = (int(nz[-1]) + 1) / OUT_RATE if nz.size else 0.0
        return {"seconds": round(secs, 3), "rms_db": round(voiced_rms_db(x), 1),
                "peak_db": round(20 * np.log10(peak), 1) if peak > 0 else float("-inf")}
    except Exception:
        return {"seconds": 0.0, "rms_db": float("-inf"), "peak_db": float("-inf")}


def is_audible(pcm: bytes) -> bool:
    """The PCM holds a real sentence: at least :data:`MIN_AUDIBLE_S` long and louder than
    :data:`MIN_RMS_DB`. An empty or silent synthesis is caught here."""
    if not pcm or len(pcm) < int(OUT_RATE * 4 * MIN_AUDIBLE_S):
        return False
    st = pcm_stats(pcm)
    return st["seconds"] >= MIN_AUDIBLE_S and st["rms_db"] >= MIN_RMS_DB


def wav_audible(wav: Any) -> bool:
    return is_audible(to_pcm48(wav))


# ------------------------------------------------------------------------------ recorded clips
def clip_name(text: Any) -> str:
    """File stem of a recorded sentence: ``"Dragon dans 30 secondes"`` -> ``dragon_dans_30_secondes``."""
    s = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def clip_dir() -> Path:
    from treeaicoach import paths  # noqa: PLC0415

    return Path(paths.asset_path("sounds", "voice"))


def recorded_wav(text: Any) -> bytes | None:
    """The clip recorded at build time for ``text`` (assets/sounds/voice), None if there is none."""
    try:
        p = clip_dir() / f"{clip_name(text)}.wav"
        return p.read_bytes() if p.is_file() else None
    except Exception:
        return None


# ------------------------------------------------------------------------------ the voice chain
def _in_thread(fn: Callable[[], Any], timeout: float, name: str) -> Any:
    """``fn()`` on a fresh daemon thread (its own COM apartment); TimeoutError after ``timeout``."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["res"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["err"] = exc
    th = threading.Thread(target=run, name=name, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        raise TimeoutError(f"{name}: plus de {timeout:.0f} s")
    if "err" in box:
        raise box["err"]
    return box.get("res")


def _com_init() -> Callable[[], None]:
    """Set this thread's COM apartment (MTA) for WinRT / SAPI; returns the matching uninit."""
    try:
        from winrt.runtime import MTA, init_apartment, uninit_apartment  # noqa: PLC0415

        try:
            init_apartment(MTA)
            return uninit_apartment
        except Exception:
            log.debug("winrt init_apartment", exc_info=True)     # already set in this thread: fine
    except Exception:
        pass
    try:
        import pythoncom  # noqa: PLC0415

        pythoncom.CoInitializeEx(0)                               # COINIT_MULTITHREADED
        return pythoncom.CoUninitialize
    except Exception:
        log.debug("CoInitializeEx", exc_info=True)
    return lambda: None


def onecore_wav(text: str) -> bytes | None:
    """Windows OneCore voice (WinRT SpeechSynthesizer) -> WAV, in its own thread with its COM
    apartment set (2.9.0 called it from a bare worker thread). Windows only."""
    if sys.platform != "win32":
        return None

    def run() -> bytes:
        uninit = _com_init()
        try:
            from treeaicoach import tts_neural  # noqa: PLC0415
            from treeaicoach.tts_lexicon import speakable  # noqa: PLC0415

            synth = tts_neural.OneCoreSynth()
            data = bytes(synth.wav(speakable(text)))
            log.info("Discord : voix Windows OneCore « %s »", getattr(synth, "voice_name", "") or "par défaut")
            del synth
            return data
        finally:
            try:
                uninit()
            except Exception:
                pass
    return _in_thread(run, STEP_TIMEOUT_S["onecore"], "TreeAI-discord-onecore")


SAPI_FORMAT_22K_16BIT_MONO = 22          # SpeechAudioFormatType SAFT22kHz16BitMono
SAPI_CREATE_FOR_WRITE = 3                # SpeechStreamFileMode SSFMCreateForWrite
_FRENCH_LCIDS = ("40c", "c0c", "80c", "100c", "140c", "180c")


def sapi_wav(text: str) -> bytes | None:
    """Windows SAPI (SpVoice) written to a WAV file (SpFileStream, 22 kHz 16-bit mono), French voice
    when one is installed. Windows only."""
    if sys.platform != "win32":
        return None

    def run() -> bytes | None:
        import pythoncom  # noqa: PLC0415
        import win32com.client  # noqa: PLC0415

        from treeaicoach.tts_lexicon import speakable  # noqa: PLC0415

        pythoncom.CoInitialize()
        fd, path = tempfile.mkstemp(prefix="treeai_bot_", suffix=".wav")
        os.close(fd)
        try:
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            try:
                for tok in voice.GetVoices():
                    lang = str(tok.GetAttribute("Language") or "").lower()
                    if any(code in lang.split(";") for code in _FRENCH_LCIDS):
                        voice.Voice = tok
                        break
            except Exception:
                log.debug("SAPI voice selection", exc_info=True)
            fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
            fmt.Type = SAPI_FORMAT_22K_16BIT_MONO
            stream = win32com.client.Dispatch("SAPI.SpFileStream")
            stream.Format = fmt
            stream.Open(path, SAPI_CREATE_FOR_WRITE, False)
            try:
                voice.AudioOutputStream = stream
                voice.Speak(speakable(text), 0)
            finally:
                stream.Close()
            return Path(path).read_bytes()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
            pythoncom.CoUninitialize()
    return _in_thread(run, STEP_TIMEOUT_S["sapi"], "TreeAI-discord-sapi")


class _Edge:
    """The natural voice (tts_neural.NeuralTTS, its WAV cache) for the bot's sentences."""

    def __init__(self) -> None:
        self._tts: Any = None
        self._params: tuple[str, str] | None = None
        self._lock = threading.Lock()

    def __call__(self, text: str, voice: str = "", rate: str = "") -> bytes | None:
        with self._lock:
            if self._params != (voice, rate):
                self._params = (voice, rate)
                self._tts = None
                try:
                    from treeaicoach import tts_neural  # noqa: PLC0415

                    if tts_neural.neural_available():
                        self._tts = tts_neural.NeuralTTS(
                            tts_neural.clean_voice_id(voice or tts_neural.DEFAULT_NEURAL_VOICE),
                            rate or tts_neural.DEFAULT_NEURAL_RATE)
                    else:
                        log.warning("Discord : voix naturelle absente de cette version (edge-tts / miniaudio)")
                except Exception:
                    log.warning("Discord : voix naturelle indisponible", exc_info=True)
            tts = self._tts
        if tts is None:
            return None
        p = tts.get(text, timeout=STEP_TIMEOUT_S["edge"])
        return p.read_bytes() if p is not None else None


Step = Callable[[str, str, str], Any]


class BotVoice:
    """Text -> ``(wav, source)`` through a chain whose every answer is VERIFIED (readable WAV, real
    sound, :func:`is_audible`) before it is used: ``edge`` -> ``onecore`` -> ``sapi`` -> ``clip``
    (recorded at build time, only for :data:`RECORDED` sentences). ``steps`` replaces the synthesis
    steps (tests, a custom voice); the recorded clips always stay as the last resort. Thread-safe."""

    def __init__(self, steps: list[tuple[str, Step]] | None = None, *, recorded: bool = True) -> None:
        if steps is None:
            steps = [("edge", _Edge()), ("onecore", lambda t, v, r: onecore_wav(t)),
                     ("sapi", lambda t, v, r: sapi_wav(t))]
        self.steps = list(steps)
        self.recorded = recorded
        self._lock = threading.Lock()
        self.last_source = ""                 # the source of the last sentence produced
        self.failures: dict[str, str] = {}    # source -> why it failed last (log / probe)

    def chain(self) -> list[tuple[str, Step]]:
        out = list(self.steps)
        if self.recorded:
            out.append(("clip", lambda t, v, r: recorded_wav(t)))
        return out

    def synth(self, text: str, voice: str = "", rate: str = "", *, skip: tuple[str, ...] = ()) -> tuple[bytes, str]:
        """The first verified WAV for ``text`` and its source, ``(b"", "")`` when every step failed.
        Blocking (seconds when the network is down): never on the bot loop. Never raises."""
        for name, fn in self.chain():
            if name in skip:
                continue
            t0 = time.monotonic()
            try:
                wav = fn(text, voice, rate)
            except Exception as exc:
                why = f"{type(exc).__name__}: {exc}"[:160]
                self.failures[name] = why
                log.warning("Discord : voix %s en échec pour « %s » (%s)", name, text, why)
                continue
            if not wav:
                self.failures[name] = "aucun son"
                log.info("Discord : voix %s sans résultat pour « %s »", name, text)
                continue
            if not wav_audible(wav):
                self.failures[name] = f"son vide ou illisible ({len(bytes(wav))} octets)"
                log.warning("Discord : voix %s a rendu un son vide ou illisible pour « %s » (%d octets)",
                            name, text, len(bytes(wav)))
                continue
            self.failures.pop(name, None)
            with self._lock:
                self.last_source = name
            log.info("Discord : « %s » par la voix %s (%.1f s de synthèse)", text, name, time.monotonic() - t0)
            return bytes(wav), name
        return b"", ""


__all__ = ["BotVoice", "DEFAULT_VOLUME", "RECORDED", "SOURCE_LABELS", "apply_volume", "chime_pcm", "clip_name",
           "is_audible", "onecore_wav", "parse_wav", "pcm_stats", "recorded_wav", "sapi_wav", "soft_limit",
           "speech_pcm", "to_pcm48", "voiced_rms_db", "volume_gain"]
