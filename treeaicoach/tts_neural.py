"""Natural French voices: Microsoft Edge "read aloud" neural voices (online) + Windows OneCore.

* :class:`NeuralTTS` synthesises a sentence with the ``edge-tts`` service (MP3, the only format
  the read-aloud endpoint serves: checked 2026-10, ``riff-24khz-16bit-mono-pcm`` /
  ``raw-*-pcm`` / ``ogg-*`` make the service close the socket without audio), decodes it once with ``miniaudio`` (no ffmpeg) and keeps a
  16-bit mono WAV in ``paths.cache_dir()/tts/<voice>/<sha1>.wav``. A cached sentence plays
  instantly; :meth:`NeuralTTS.prefetch` fills the cache in the background (every sentence the
  coach may say for the 10 champions of the game: see :func:`roster_phrases`).
* :func:`onecore_wav` synthesises with the Windows 10/11 OneCore voices (WinRT
  ``Windows.Media.SpeechSynthesis`` through pywinrt) -> WAV bytes, offline.
* :class:`WavPlayer` plays a WAV file with ``winsound`` (``SND_FILENAME | SND_ASYNC``; winsound
  refuses ``SND_ASYNC`` from memory), volume applied by scaling the samples (numpy), stop =
  ``PlaySound(None, 0)``. ``is_playing`` is estimated from the WAV duration.

Everything is optional and lazy: a missing package / no network / no audio only makes the
functions return ``None``; nothing here raises to the caller.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import tempfile
import threading
import time
import wave
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future
from concurrent.futures import TimeoutError as _FutureTimeout
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_NEURAL_VOICE = "fr-FR-DeniseNeural"
#: (voice id, label shown in the UI)
NEURAL_VOICES: tuple[tuple[str, str], ...] = (
    ("fr-FR-DeniseNeural", "Denise (femme, France)"),
    ("fr-FR-HenriNeural", "Henri (homme, France)"),
    ("fr-FR-VivienneMultilingualNeural", "Vivienne (femme, France)"),
    ("fr-FR-RemyMultilingualNeural", "Rémy (homme, France)"),
    ("fr-FR-EloiseNeural", "Éloïse (jeune femme, France)"),
    ("fr-CA-SylvieNeural", "Sylvie (femme, Québec)"),
    ("fr-CA-JeanNeural", "Jean (homme, Québec)"),
    ("fr-BE-CharlineNeural", "Charline (femme, Belgique)"),
    ("fr-CH-ArianeNeural", "Ariane (femme, Suisse)"),
)
DEFAULT_NEURAL_RATE = "+15%"
LIVE_TIMEOUT_S = 1.5          # a non-cached sentence waits at most this long for the service
BACKGROUND_TIMEOUT_S = 12.0   # a synthesis left running in the background (it fills the cache)
OFFLINE_RETRY_S = 45.0        # after a failure, live synthesis is skipped for this long
PREFETCH_CONCURRENCY = 3
SAMPLE_RATE = 24000
MAX_PREFETCH = 600
CACHE_VERSION = "1"


def rate_percent(rate: Any) -> int:
    """Service rate in %: ``"+15%"`` / ``"-10%"`` (config ``neural_rate``) is used as is
    (clamped to -50..+100); a number is a legacy SAPI rate (-10..10, 2 -> +15 %)."""
    if isinstance(rate, str):
        s = rate.strip().rstrip("%").strip()
        try:
            return max(-50, min(100, int(round(float(s)))))
        except (TypeError, ValueError, OverflowError):
            return 15
    try:
        r = max(-10, min(10, int(round(float(rate)))))
    except (TypeError, ValueError, OverflowError):
        r = 2
    return int(round(r * 7.5))


def clean_voice_id(value: Any) -> str:
    """A plausible service voice id (``xx-YY-NameNeural``), else the default voice."""
    if isinstance(value, str):
        v = value.strip()
        if 6 <= len(v) <= 80 and v.count("-") >= 2 and v.endswith("Neural") \
                and all(c.isalnum() or c == "-" for c in v):
            return v
    return DEFAULT_NEURAL_VOICE


def rate_string(rate: Any) -> str:
    """Canonical service rate string (``"+15%"``)."""
    return f"{rate_percent(rate):+d}%"


def list_neural_voices() -> list[tuple[str, str]]:
    """``[(voice id, French label), ...]`` offered in the settings (Denise first)."""
    return list(NEURAL_VOICES)


def neural_available() -> bool:
    """True when the packages needed by :class:`NeuralTTS` are importable."""
    try:
        import edge_tts  # noqa: F401, PLC0415
        import miniaudio  # noqa: F401, PLC0415
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------------------
# WAV helpers
# --------------------------------------------------------------------------------------

def pcm_to_wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm)
    return bio.getvalue()


def mp3_to_wav(mp3: bytes) -> bytes | None:
    """Decode MP3 bytes to a 16-bit mono WAV (miniaudio). None if impossible."""
    if not mp3:
        return None
    try:
        import miniaudio  # noqa: PLC0415

        snd = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
                               sample_rate=SAMPLE_RATE)
        pcm = snd.samples.tobytes()
        if len(pcm) < 200:
            return None
        return pcm_to_wav(_trim_silence(pcm), SAMPLE_RATE)
    except Exception as exc:
        log.debug("MP3 decode failed: %s", exc)
        return None


def _trim_silence(pcm: bytes, threshold: int = 120, keep_ms: int = 30) -> bytes:
    """Remove the leading / trailing silence of the service (≈ 100 ms each: latency!)."""
    try:
        import numpy as np  # noqa: PLC0415

        a = np.frombuffer(pcm, dtype="<i2")
        loud = np.flatnonzero(np.abs(a.astype(np.int32)) > threshold)
        if loud.size == 0:
            return pcm
        keep = int(SAMPLE_RATE * keep_ms / 1000)
        lo = max(0, int(loud[0]) - keep)
        hi = min(a.size, int(loud[-1]) + 1 + 3 * keep)
        return a[lo:hi].tobytes()
    except Exception:
        return pcm


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 0.0


def scale_wav(src: Path, dst: Path, volume: int) -> bool:
    """Copy a 16-bit WAV with its samples multiplied by ``volume`` / 100 (atomic)."""
    tmp = Path(f"{dst}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        import numpy as np  # noqa: PLC0415

        with wave.open(str(src), "rb") as r:
            params = r.getparams()
            frames = r.readframes(r.getnframes())
        if params.sampwidth != 2:
            return False
        a = np.frombuffer(frames, dtype="<i2").astype(np.float32) * (max(0, min(100, volume)) / 100.0)
        out = np.clip(a, -32768, 32767).astype("<i2").tobytes()
        dst.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(params.nchannels)
            w.setsampwidth(2)
            w.setframerate(params.framerate)
            w.writeframes(out)
        os.replace(tmp, dst)
        return True
    except Exception as exc:
        log.debug("scale_wav failed: %s", exc)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _write_atomic(path: Path, data: bytes) -> bool:
    tmp = Path(f"{path}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return True
    except Exception as exc:
        log.debug("Cannot write %s: %s", path, exc)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def tts_cache_root() -> Path:
    try:
        from treeaicoach import paths  # noqa: PLC0415

        return Path(paths.cache_dir()) / "tts"
    except Exception:
        return Path(tempfile.gettempdir()) / "TreeAICoach" / "tts"


# --------------------------------------------------------------------------------------
# Playback (winsound, Windows only; tests inject a fake play function)
# --------------------------------------------------------------------------------------

class WavPlayer:
    """Asynchronous WAV playback with ``winsound``. Used from the voice thread only."""

    def __init__(self, *, _play: Callable[[str | None], None] | None = None,
                 _clock: Callable[[], float] = time.monotonic) -> None:
        self._play = _play or _winsound_play
        self._clock = _clock
        self._until = 0.0

    def play(self, path: Path, volume: int = 100) -> bool:
        vol = max(0, min(100, int(volume)))
        if vol <= 0:
            return True
        src = Path(path)
        if vol < 98:
            bucket = max(5, int(round(vol / 5.0)) * 5)
            scaled = src.with_name(f"{src.stem}_v{bucket}.wav")
            if not scaled.is_file() and not scale_wav(src, scaled, bucket):
                scaled = src
            src = scaled
        dur = wav_duration(src)
        if dur <= 0:
            return False
        try:
            self._play(str(src))
        except Exception as exc:
            log.warning("Lecture audio impossible : %s", exc)
            return False
        self._until = float(self._clock()) + dur + 0.05
        return True

    def is_playing(self) -> bool:
        return float(self._clock()) < self._until

    def stop(self) -> None:
        if self._until <= 0:
            return
        self._until = 0.0
        try:
            self._play(None)
        except Exception as exc:
            log.debug("Stop playback failed: %s", exc)


def _winsound_play(path: str | None) -> None:
    import winsound  # noqa: PLC0415 - Windows only

    if path is None:
        winsound.PlaySound(None, 0)
    else:
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


# --------------------------------------------------------------------------------------
# Edge neural voices
# --------------------------------------------------------------------------------------

_SSL_PATCHED = False


def _patch_ssl() -> None:
    """Also trust the Windows certificate store (antivirus HTTPS scanning, corporate proxy)."""
    global _SSL_PATCHED
    if _SSL_PATCHED:
        return
    _SSL_PATCHED = True
    try:
        from edge_tts import communicate  # noqa: PLC0415

        ctx = getattr(communicate, "_SSL_CTX", None)
        if ctx is None:
            return
        try:
            ctx.load_default_certs()
        except Exception:
            pass
        for env in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
            p = os.environ.get(env)
            if p and os.path.isfile(p):
                try:
                    ctx.load_verify_locations(p)
                except Exception:
                    pass
    except Exception as exc:
        log.debug("edge-tts SSL patch skipped: %s", exc)


async def _edge_mp3(text: str, voice: str, rate_pct: int) -> bytes:
    import edge_tts  # noqa: PLC0415

    comm = edge_tts.Communicate(text, voice, rate=f"{rate_pct:+d}%", connect_timeout=5,
                                receive_timeout=8)
    out = bytearray()
    async for chunk in comm.stream():
        if chunk.get("type") == "audio":
            out += chunk.get("data") or b""
    return bytes(out)


class _Loop:
    """One asyncio loop in a daemon thread, shared by every NeuralTTS (lazy)."""

    _lock = threading.Lock()
    _loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def get(cls) -> asyncio.AbstractEventLoop:
        with cls._lock:
            if cls._loop is None or cls._loop.is_closed():
                loop = asyncio.new_event_loop()
                th = threading.Thread(target=loop.run_forever, name="TreeAICoach-tts", daemon=True)
                th.start()
                cls._loop = loop
            return cls._loop


class NeuralTTS:
    """Edge neural voice with a WAV disk cache. Thread-safe."""

    def __init__(self, voice: str = DEFAULT_NEURAL_VOICE, rate: int = 2, *,
                 cache_root: Path | None = None,
                 _synth: Callable[[str, str, int], bytes | None] | None = None,
                 _clock: Callable[[], float] = time.monotonic) -> None:
        self.voice = clean_voice_id(voice)
        self.rate_pct = rate_percent(rate)
        self._root = Path(cache_root) if cache_root is not None else tts_cache_root()
        self._synth = _synth           # tests: blocking fake (text, voice, rate%) -> mp3/wav bytes
        self._clock = _clock
        self._lock = threading.Lock()
        self._inflight: dict[str, Future[Path | None]] = {}
        self._offline_until = 0.0
        self._prefetch_gen = 0
        self.failures = 0
        _patch_ssl()

    # -- cache ------------------------------------------------------------------------
    def set_params(self, voice: str, rate: int) -> None:
        with self._lock:
            self.voice = clean_voice_id(voice)
            self.rate_pct = rate_percent(rate)

    def key(self, text: str) -> str:
        raw = f"{CACHE_VERSION}|{self.voice}|{self.rate_pct}|{text}".encode()
        return hashlib.sha1(raw).hexdigest()

    def path_for(self, text: str) -> Path:
        return self._root / self.voice / f"{self.key(text)}.wav"

    def cached(self, text: str) -> Path | None:
        p = self.path_for(text)
        try:
            return p if p.is_file() and p.stat().st_size > 1000 else None
        except OSError:
            return None

    def online(self) -> bool:
        return float(self._clock()) >= self._offline_until

    # -- synthesis --------------------------------------------------------------------
    def _produce(self, text: str, voice: str, rate_pct: int, dest: Path) -> Path | None:
        """Blocking synthesis -> cache file (runs in a worker thread / the asyncio loop)."""
        try:
            if self._synth is not None:
                data = self._synth(text, voice, rate_pct)
            else:
                loop = _Loop.get()
                fut = asyncio.run_coroutine_threadsafe(
                    asyncio.wait_for(_edge_mp3(text, voice, rate_pct), BACKGROUND_TIMEOUT_S), loop)
                data = fut.result(BACKGROUND_TIMEOUT_S + 1.0)
            wav = data if data and data[:4] == b"RIFF" else mp3_to_wav(data or b"")
            if not wav or not _write_atomic(dest, wav):
                raise RuntimeError("no audio")
            with self._lock:
                self.failures = 0
                self._offline_until = 0.0
            return dest
        except Exception as exc:
            with self._lock:
                self.failures += 1
                self._offline_until = float(self._clock()) + OFFLINE_RETRY_S
            log.warning("Voix naturelle indisponible (%s) : voix Windows utilisée pendant %.0f s.",
                        type(exc).__name__ + (f": {exc}" if str(exc) else ""), OFFLINE_RETRY_S)
            return None

    def _start(self, text: str) -> Future[Path | None]:
        with self._lock:
            voice, rate = self.voice, self.rate_pct
            dest = self._root / voice / f"{self.key(text)}.wav"
            key = str(dest)
            fut = self._inflight.get(key)
            if fut is not None:
                return fut
            fut = Future()
            self._inflight[key] = fut

        def job() -> None:
            res: Path | None = None
            try:
                res = self._produce(text, voice, rate, dest)
            finally:
                with self._lock:
                    self._inflight.pop(key, None)
                fut.set_result(res)

        threading.Thread(target=job, name="TreeAICoach-tts-job", daemon=True).start()
        return fut

    def get(self, text: str, timeout: float = LIVE_TIMEOUT_S,
            abort: Callable[[], bool] | None = None) -> Path | None:
        """Cached WAV, else synthesise for at most ``timeout`` s (the job keeps filling the cache).
        ``timeout <= 0``: cache only (the synthesis is started in the background for next time).
        ``abort()`` returning True (an urgent message is waiting) stops the wait at once."""
        p = self.cached(text)
        if p is not None:
            return p
        if not self.online():
            return None
        fut = self._start(text)
        if timeout <= 0:
            return None
        deadline = time.monotonic() + timeout
        try:
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise _FutureTimeout
                try:
                    return fut.result(min(left, 0.02) if abort is not None else left)
                except _FutureTimeout:
                    if abort is not None and abort():
                        return None
        except Exception:
            log.info("Voix naturelle trop lente (> %.1f s) pour : %s", timeout, text)
            return None

    def prefetch(self, texts: Iterable[str]) -> None:
        """Fill the cache with ``texts`` in a background thread (a newer call replaces it)."""
        items: list[str] = []
        seen: set[str] = set()
        for t in texts:
            if isinstance(t, str) and t and t not in seen:
                seen.add(t)
                items.append(t)
            if len(items) >= MAX_PREFETCH:
                break
        with self._lock:
            self._prefetch_gen += 1
            gen = self._prefetch_gen

        def run() -> None:
            t0, done, made = time.monotonic(), 0, 0
            pending: list[Future[Path | None]] = []
            for text in items:
                with self._lock:
                    if gen != self._prefetch_gen:
                        return
                if self.cached(text) is not None:
                    done += 1
                    continue
                if not self.online():
                    time.sleep(2.0)
                    if not self.online():
                        log.info("Pré-génération de la voix interrompue (hors ligne) : %d/%d prêtes.",
                                 done, len(items))
                        return
                pending.append(self._start(text))
                if len(pending) >= PREFETCH_CONCURRENCY:
                    if pending.pop(0).result() is not None:
                        made += 1
                    done += 1
            for f in pending:
                if f.result() is not None:
                    made += 1
                done += 1
            log.info("Voix naturelle : %d phrases prêtes (%d générées en %.1f s).",
                     done, made, time.monotonic() - t0)

        if items:
            threading.Thread(target=run, name="TreeAICoach-tts-prefetch", daemon=True).start()


# --------------------------------------------------------------------------------------
# Windows OneCore voices (WinRT)
# --------------------------------------------------------------------------------------

class OneCoreSynth:
    """WinRT ``SpeechSynthesizer`` -> WAV bytes. Create and use in one thread."""

    def __init__(self) -> None:
        from winrt.windows.media.speechsynthesis import SpeechSynthesizer  # noqa: PLC0415

        self._synth = SpeechSynthesizer()
        self._voices = list(SpeechSynthesizer.all_voices)
        self.voice_name = ""
        self.select("")

    def voices(self) -> list[str]:
        out = []
        for v in self._voices:
            try:
                out.append(str(v.display_name))
            except Exception:
                pass
        return out

    def select(self, wanted: str) -> None:
        want = (wanted or "").casefold()
        best, best_score = None, -1
        for v in self._voices:
            try:
                name, lang = str(v.display_name), str(v.language)
            except Exception:
                continue
            score = 0
            if want and want in name.casefold():
                score = 1000
            if lang.casefold() == "fr-fr":
                score += 100
            elif lang.casefold().startswith("fr"):
                score += 80
            if "denise" in name.casefold() or "julie" in name.casefold():
                score += 5
            if score > best_score:
                best, best_score = v, score
        if best is not None and best_score >= 80:
            try:
                self._synth.voice = best
                self.voice_name = str(best.display_name)
            except Exception as exc:
                log.debug("OneCore voice selection failed: %s", exc)

    def configure(self, rate: int, volume: int) -> None:
        try:
            opts = self._synth.options
            opts.speaking_rate = max(0.5, min(3.0, 1.0 + 0.06 * int(rate)))
            opts.audio_volume = max(0.0, min(1.0, int(volume) / 100.0))
        except Exception as exc:
            log.debug("OneCore options not applied: %s", exc)

    def wav(self, text: str) -> bytes:
        return asyncio.run(self._wav_async(text))

    async def _wav_async(self, text: str) -> bytes:
        from winrt.windows.storage.streams import DataReader  # noqa: PLC0415

        stream = await self._synth.synthesize_text_to_stream_async(text)
        size = int(stream.size)
        reader = DataReader(stream.get_input_stream_at(0))
        await reader.load_async(size)
        try:
            data = bytes(reader.read_buffer(size))
        except Exception:
            buf = bytearray(size)
            reader.read_bytes(buf)
            data = bytes(buf)
        if data[:4] != b"RIFF":
            raise RuntimeError("OneCore: unexpected audio format")
        return data


# --------------------------------------------------------------------------------------
# Phrases to pre-generate
# --------------------------------------------------------------------------------------

DIRECTIONS = ("par la rivière", "par ta jungle", "par la jungle ennemie", "par la jungle",
              "par le haut", "par le milieu", "par le bas")


def static_phrases(leads: Sequence[int] = (60, 20)) -> list[str]:
    """Sentences without a champion name (objectives, collapses, reminders)."""
    out: list[str] = []
    try:
        from treeaicoach.alerts import AlertKind, Level, phrase  # noqa: PLC0415

        for lvl in (Level.DANGER, Level.WARNING):
            for n in range(2, 6):
                out.append(phrase(AlertKind.COLLAPSE, lvl, None, count=n))
            out.append(phrase(AlertKind.COLLAPSE, lvl, None, count=0))
            out.append(phrase(AlertKind.JUNGLER_APPROACH, lvl, None))
            out.append(phrase(AlertKind.ROAM_APPROACH, lvl, None))
            out.append(phrase(AlertKind.LANER_MIA, lvl, None))
        out.append(phrase(AlertKind.PERSONAL_DANGER, Level.DANGER, None))      # "Recule !" (danger.py)
        out.append(phrase(AlertKind.CONTROL_WARD, Level.INFO, None))
    except Exception as exc:
        log.debug("static_phrases (alerts) failed: %s", exc)
    try:
        from treeaicoach.game_changers import voice_phrases  # noqa: PLC0415

        out.extend(voice_phrases())              # the game-changer calls: static, pre-generated
    except Exception as exc:
        log.debug("static_phrases (game changers) failed: %s", exc)
    try:
        from treeaicoach.objectives import NAMES_FR, announcement_text  # noqa: PLC0415

        for kind in NAMES_FR:
            for lead in leads:
                out.append(announcement_text(kind, int(lead)))
    except Exception as exc:
        log.debug("static_phrases (objectives) failed: %s", exc)
    return out


def roster_phrases(enemies: Sequence[str], allies: Sequence[str] = ()) -> list[str]:
    """Every gank sentence the coach may say for these enemy champions, most urgent first
    (``gank.py`` emits exactly these: tests/test_latency.py checks it)."""
    names = [n for n in (str(x).strip() for x in enemies if x) if n][:5]
    danger: list[str] = []
    warn: list[str] = []
    other: list[str] = []
    try:
        from treeaicoach.alerts import AlertKind, Level, phrase  # noqa: PLC0415
        from treeaicoach.gank import pre_alert_text  # noqa: PLC0415

        lanes = ("top", "mid", "bot", None)
        for name in names:
            danger.append(phrase(AlertKind.JUNGLER_APPROACH, Level.DANGER, name))
            danger.append(pre_alert_text(name))
            danger.append(phrase(AlertKind.ROAM_APPROACH, Level.DANGER, name))      # "Roam ! Ekko, recule !"
            danger.append(phrase(AlertKind.COLLAPSE, Level.DANGER, name, count=1))
            for d in DIRECTIONS:
                warn.append(phrase(AlertKind.JUNGLER_APPROACH, Level.WARNING, name, d))
            warn.append(phrase(AlertKind.JUNGLER_APPROACH, Level.WARNING, name))
            for d in DIRECTIONS:
                warn.append(phrase(AlertKind.ROAM_APPROACH, Level.WARNING, name, d))
            warn.append(phrase(AlertKind.ROAM_APPROACH, Level.WARNING, name))
            warn.append(phrase(AlertKind.COLLAPSE, Level.WARNING, name, count=1))
            warn.append(phrase(AlertKind.LANER_MIA, Level.DANGER, name))
            warn.append(phrase(AlertKind.LANER_MIA, Level.WARNING, name))
            other.append(phrase(AlertKind.ROAM_APPROACH, Level.INFO, name))
            other.append(phrase(AlertKind.LANER_MIA, Level.INFO, name))
            for lvl in (Level.DANGER, Level.WARNING):
                for lane in lanes:
                    # merged gank with an anonymous second enemy ("Gank top : Lee Sin et un ennemi !")
                    (danger if lvl >= Level.DANGER else warn).append(
                        phrase(AlertKind.COLLAPSE, lvl, None, lane, count=2, names=[name]))
                    other.append(phrase(AlertKind.COLLAPSE, lvl, None, lane, count=1, names=[name]))
        # merged duos (both orders: the jungler is named first, whoever it is)
        for a in names:
            for b in names:
                if a == b:
                    continue
                for lvl in (Level.DANGER, Level.WARNING):
                    for lane in lanes:
                        (danger if lvl >= Level.DANGER else warn).append(
                            phrase(AlertKind.COLLAPSE, lvl, None, lane, count=2, names=[a, b]))
    except Exception as exc:
        log.debug("roster_phrases failed: %s", exc)
    return danger + warn + other


def build_phrase_list(game: Any) -> list[str]:
    """Every sentence worth pre-generating for this game (``live_client.GameState``-like:
    ``enemies`` / ``allies`` lists of players with ``champion_name``), most urgent first:
    gank / collapse / MIA alerts for the 5 enemies, then objectives and generic alerts.
    Never raises (``[]`` on garbage)."""
    def names(players: Any) -> list[str]:
        out: list[str] = []
        try:
            for p in list(players or ())[:5]:
                n = getattr(p, "champion_name", None)
                if n is None and isinstance(p, dict):
                    n = p.get("champion_name") or p.get("championName")
                n = str(n or "").strip()
                if n and n not in out:
                    out.append(n)
        except Exception:
            pass
        return out

    try:
        out: list[str] = []
        seen: set[str] = set()
        for text in roster_phrases(names(getattr(game, "enemies", ())),
                                   names(getattr(game, "allies", ()))) + static_phrases():
            if isinstance(text, str) and text and text not in seen:
                seen.add(text)
                out.append(text)
        return out[:MAX_PREFETCH]
    except Exception as exc:
        log.debug("build_phrase_list failed: %s", exc)
        return []
