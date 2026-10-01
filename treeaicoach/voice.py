"""Text-to-speech in a dedicated thread, with a small priority-aware queue.

* Engines (``engine`` / ``cfg.voice_engine``): ``"auto"`` = ``"neural"`` (Microsoft Edge neural
  voice, online, sentences cached as WAV and pre-generated for the champions of the game:
  :meth:`VoiceEngine.prewarm` / :meth:`VoiceEngine.prefetch`, see :mod:`treeaicoach.tts_neural`)
  -> ``"sapi"`` -> ``"print"``; ``"onecore"`` (Windows 10/11 OneCore voices through WinRT) only
  when chosen explicitly. A neural sentence that is not cached and not synthesised within
  ~1.5 s (0.9 s for a DANGER) is said by SAPI instead (the synthesis keeps filling the cache).

* :class:`VoiceEngine` is the public entry point. :meth:`VoiceEngine.say` is thread-safe and
  never blocks: it only appends to a queue consumed by a daemon thread.
* Queue rules: a DANGER message (level 2) drops everything queued before it, cuts the current
  sentence (``SVSFPurgeBeforeSpeak`` / ``PlaySound(None)``) and is optionally preceded by a short
  double beep that starts at once; a WARNING goes ahead of the less urgent queued messages and
  cuts a less urgent sentence being spoken; other messages wait for the current sentence to
  finish; a message queued for more than 2.5 s is dropped (it is no longer relevant in a fight).
  WARNING / DANGER sentences never wait for the network: cached neural WAV, else the local
  (SAPI) voice at once, the neural synthesis filling the cache in the background
  (``NeuralBackend.speak_urgent``); a network wait for a less urgent sentence stops as soon as
  a gank alert is queued.
* Backends (:class:`SpeechBackend`): ``"sapi"`` (:class:`SapiBackend`, Windows, pywin32:
  ``pythoncom.CoInitialize()`` + ``win32com.client.Dispatch("SAPI.SpVoice")`` created and used
  in the voice thread only) and ``"print"`` (:class:`PrintBackend`, logs the text). Anything
  failing in SAPI (no pywin32, COM error, no audio...) falls back to ``"print"``: the voice
  never raises and never blocks the caller.
* The best French voice is chosen automatically (language id 0x..0C such as ``40C``, or a
  description containing French / Français / Hortense / Julie / Paul / Claude), including the
  Windows 10/11 "OneCore" voices when SAPI accepts them.
* The danger beep is a small WAV generated with the stdlib :mod:`wave` module in
  ``paths.cache_dir()/sounds`` (temp dir as a fallback), played with ``winsound``.

Tests inject a fake backend with ``VoiceEngine(..., _backend_factory=...)``.
"""

from __future__ import annotations

import array
import logging
import math
import os
import re
import sys
import tempfile
import threading
import time
import wave
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Levels (same values as alerts.Level; not imported to keep this module standalone).
LEVEL_INFO = 0
LEVEL_WARNING = 1
LEVEL_DANGER = 2

MAX_AGE_S = 2.5            # a message waiting longer than this in the queue is dropped
MAX_QUEUE = 8              # bounded queue (oldest non-danger message dropped first)
MAX_TEXT_LEN = 300
MAX_UTTERANCE_S = 15.0     # a sentence "speaking" longer than this is considered stuck
POLL_S = 0.05              # polling period while a sentence is being spoken
IDLE_WAIT_S = 0.25         # wake-up period when idle (params / pump)
STOP_TIMEOUT_S = 2.0
LIST_VOICES_TIMEOUT_S = 5.0
MAX_SPEAK_FAILURES = 3     # consecutive failures before falling back to the "print" backend
ERROR_LOG_EVERY_S = 10.0

RATE_RANGE = (-10, 10)
VOLUME_RANGE = (0, 100)
DEFAULT_RATE = 2
DEFAULT_VOLUME = 100
VOICE_NAME_MAX_LEN = 256

# SAPI SpeechVoiceSpeakFlags / SpeechRunState
SVSF_ASYNC = 1
SVSF_PURGE_BEFORE_SPEAK = 2
SVSF_IS_NOT_XML = 16
SRSE_IS_SPEAKING = 2
ONECORE_VOICES_KEY = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"
LANG_FRENCH_PRIMARY = 0x0C     # PRIMARYLANGID of every French LANGID (40C, C0C, 80C, 100C...)
LANG_FR_FR = 0x040C
FRENCH_KEYWORDS: tuple[str, ...] = (
    "french", "français", "francais", "hortense", "julie", "paul", "claude", "fr-fr", "fr-ca",
)

# Danger beep: 2 x 90 ms at 1200 Hz
BEEP_FREQ_HZ = 1200
BEEP_TONE_MS = 90
BEEP_GAP_MS = 70
BEEP_COUNT = 2
BEEP_SAMPLE_RATE = 22050
BEEP_EDGE_MS = 10          # silence before / after (some audio drivers clip the first ms)
BEEP_FADE_MS = 6           # raised-cosine fade in/out (no click)
BEEP_PEAK = 0.55           # full-scale fraction at volume 100
BEEP_DURATION_S = (2 * BEEP_EDGE_MS + BEEP_COUNT * BEEP_TONE_MS + (BEEP_COUNT - 1) * BEEP_GAP_MS) / 1000.0
BUNDLED_BEEP = "danger.wav"    # optional override: assets/sounds/danger.wav

_WS_RE = re.compile(r"\s+")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------

def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    """Rounded int clamped to [lo, hi]; garbage / NaN / bool -> default."""
    if isinstance(value, bool):
        return default
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(f):
        return default
    return int(min(max(round(f), lo), hi))


def _clean_text(value: Any, max_len: int = MAX_TEXT_LEN) -> str:
    """One-line trimmed text (control characters removed), bounded; '' if unusable."""
    if value is None:
        return ""
    try:
        s = value if isinstance(value, str) else str(value)
    except Exception:
        return ""
    s = _WS_RE.sub(" ", _CTRL_RE.sub(" ", s)).strip()
    return s[:max_len].rstrip() if len(s) > max_len else s


def _finite_nonneg(value: Any, default: float) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0.0, f) if math.isfinite(f) else default


def _clean_voice_name(value: Any) -> str:
    return _clean_text(value, VOICE_NAME_MAX_LEN) if isinstance(value, str) else ""


_NEURAL_RATE_RE = re.compile(r"([+-]?)(\d{1,3})(?:\.\d+)?\s*%?")


def _coerce_neural_rate(value: Any, default: str = "+15%") -> str:
    """``"+15%"`` / ``"-10 %"`` / ``15`` -> ``"+15%"`` clamped to -50..+100; garbage -> default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return default
        n = int(round(value))
    elif isinstance(value, str):
        m = _NEURAL_RATE_RE.fullmatch(value.strip())
        if m is None:
            return default
        n = int(m.group(2)) * (-1 if m.group(1) == "-" else 1)
    else:
        return default
    return f"{max(-50, min(100, n)):+d}%"


def _coerce_level(value: Any) -> int:
    return _clamp_int(value, LEVEL_INFO, LEVEL_DANGER, LEVEL_WARNING)


# --------------------------------------------------------------------------------------
# Voice selection (pure, testable everywhere)
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class VoiceInfo:
    """A SAPI voice token as seen by the selection logic."""

    description: str
    languages: tuple[int, ...] = ()
    source: str = "sapi5"          # "sapi5" | "onecore"
    token_id: str = ""
    is_default: bool = False       # the Windows default voice


def parse_language_ids(attr: Any) -> tuple[int, ...]:
    """SAPI ``Language`` attribute (hex LANGIDs, e.g. ``"40C"`` or ``"40c;409"``) -> ints."""
    if not isinstance(attr, str):
        return ()
    out: list[int] = []
    for part in re.split(r"[;,\s]+", attr.strip()):
        if not part:
            continue
        try:
            out.append(int(part, 16))
        except ValueError:
            continue
    return tuple(out)


def french_score(info: VoiceInfo) -> int:
    """Preference score of a voice for French speech (0 = not French)."""
    score = 0
    if LANG_FR_FR in info.languages:
        score = 100
    elif any((lang & 0x3FF) == LANG_FRENCH_PRIMARY for lang in info.languages):
        score = 90
    else:
        desc = info.description.casefold()
        if any(k in desc for k in FRENCH_KEYWORDS):
            score = 60
    if score == 0:
        return 0
    if info.source == "sapi5":
        score += 5                 # classic SAPI voices are the most reliable through SAPI
    if info.is_default:
        score += 3                 # the user's own choice in Windows settings
    return score


def is_french_voice(info: VoiceInfo) -> bool:
    return french_score(info) > 0


def rank_voices(voices: Sequence[VoiceInfo], wanted: str = "") -> list[int]:
    """Indices of the acceptable voices, most preferred first.

    ``wanted`` (the configured voice description) is matched exactly (case-insensitive), then
    as a substring; the French voices follow as fallbacks. Non-French voices are only
    returned when explicitly wanted (otherwise the Windows default voice is kept).
    """
    want = _clean_voice_name(wanted).casefold()
    order: list[int] = []
    if want:
        exact = [i for i, v in enumerate(voices) if v.description.casefold() == want]
        partial = [
            i for i, v in enumerate(voices)
            if i not in exact and v.description
            and (want in v.description.casefold() or v.description.casefold() in want)
        ]
        partial.sort(key=lambda i: (-french_score(voices[i]), i))
        order.extend(exact + partial)
    french = [i for i, v in enumerate(voices) if i not in order and french_score(v) > 0]
    french.sort(key=lambda i: (-french_score(voices[i]), i))
    order.extend(french)
    return order


def pick_voice(voices: Sequence[VoiceInfo], wanted: str = "") -> int | None:
    """Index of the voice to use, or None (keep the Windows default voice)."""
    order = rank_voices(voices, wanted)
    return order[0] if order else None


# --------------------------------------------------------------------------------------
# Danger beep (WAV generated with the stdlib)
# --------------------------------------------------------------------------------------

def make_beep_wav(path: Path, volume: int = DEFAULT_VOLUME) -> bool:
    """Write the danger double beep as a 16-bit mono WAV (atomic). Never raises."""
    vol = _clamp_int(volume, *VOLUME_RANGE, DEFAULT_VOLUME)
    amp = BEEP_PEAK * 32767.0 * vol / 100.0
    rate = BEEP_SAMPLE_RATE
    tone_n = int(rate * BEEP_TONE_MS / 1000)
    fade_n = max(1, int(rate * BEEP_FADE_MS / 1000))
    tone = array.array("h")
    for i in range(tone_n):
        env = 1.0
        if i < fade_n:
            env = 0.5 - 0.5 * math.cos(math.pi * i / fade_n)
        elif i >= tone_n - fade_n:
            env = 0.5 - 0.5 * math.cos(math.pi * (tone_n - 1 - i) / fade_n)
        tone.append(int(round(amp * env * math.sin(2.0 * math.pi * BEEP_FREQ_HZ * i / rate))))
    edge = array.array("h", [0]) * int(rate * BEEP_EDGE_MS / 1000)
    gap = array.array("h", [0]) * int(rate * BEEP_GAP_MS / 1000)
    samples = array.array("h")
    samples.extend(edge)
    for k in range(BEEP_COUNT):
        if k:
            samples.extend(gap)
        samples.extend(tone)
    samples.extend(edge)
    if sys.byteorder == "big":
        samples.byteswap()          # WAV data is little-endian
    tmp = Path(f"{path}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(samples.tobytes())
        os.replace(tmp, path)
        return True
    except Exception as exc:
        log.debug("Cannot write beep WAV %s: %s", path, exc)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _sounds_dir() -> Path:
    """``paths.cache_dir()/sounds`` (paths imported lazily), temp dir as a fallback."""
    try:
        from treeaicoach import paths  # lazy: written in parallel, must not break this module

        d = Path(paths.cache_dir()) / "sounds"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception as exc:
        log.debug("cache_dir() unavailable (%s), using the temp dir for sounds", exc)
    return Path(tempfile.gettempdir()) / "TreeAICoach" / "sounds"


def _bundled_beep() -> Path | None:
    try:
        from treeaicoach import paths

        p = Path(paths.asset_path("sounds", BUNDLED_BEEP))
        return p if p.is_file() else None
    except Exception:
        return None


def danger_beep_path(volume: int = DEFAULT_VOLUME) -> Path | None:
    """WAV file of the danger beep for this volume (generated if missing); None if silent/failed."""
    vol = _clamp_int(volume, *VOLUME_RANGE, DEFAULT_VOLUME)
    bucket = int(round(vol / 10.0)) * 10          # few files: one per 10 % of volume
    if bucket <= 0:
        return None
    bundled = _bundled_beep()
    if bundled is not None:
        return bundled
    path = _sounds_dir() / f"danger_beep_v{bucket}.wav"
    try:
        if path.is_file() and path.stat().st_size > 44:
            return path
    except OSError:
        pass
    return path if make_beep_wav(path, bucket) else None


# --------------------------------------------------------------------------------------
# Backends (all methods are called from the voice thread only)
# --------------------------------------------------------------------------------------

class SpeechBackend:
    """Interface of a speech backend (default implementations do nothing)."""

    name: str = "print"

    def configure(self, voice_name: str, rate: int, volume: int) -> None:
        """Select the voice and apply rate (-10..10) / volume (0..100)."""

    def speak(self, text: str, purge: bool) -> None:
        """Start speaking ``text`` asynchronously; ``purge`` cuts the current sentence first."""

    def is_speaking(self) -> bool:
        return False

    def purge(self) -> None:
        """Stop the current sentence."""

    def beep(self, volume: int) -> float:
        """Start the danger beep; return how long to wait before speaking (s)."""
        return 0.0

    def voices(self) -> list[str]:
        return []

    def pump(self) -> None:
        """Process pending window messages (COM single-threaded apartment)."""

    def close(self) -> None:
        """Release resources."""


class PrintBackend(SpeechBackend):
    """Fallback without audio: the messages are written to the log."""

    name = "print"

    def speak(self, text: str, purge: bool) -> None:
        log.info("Voix : %s", text)

    def beep(self, volume: int) -> float:
        log.debug("Voix : bip de danger")
        return 0.0


class SapiBackend(SpeechBackend):
    """Windows SAPI 5 through pywin32. Must be created, used and closed in ONE thread."""

    name = "sapi"

    def __init__(self) -> None:
        import pythoncom  # noqa: PLC0415 - Windows only, lazy
        import win32com.client  # noqa: PLC0415

        self._pythoncom: Any = pythoncom
        self._client: Any = win32com.client
        pythoncom.CoInitialize()
        self._com_initialized = True
        self._sp: Any = None
        self._tokens: list[tuple[VoiceInfo, Any]] = []
        self._bad: set[str] = set()
        self._wanted: str | None = None
        self._current: VoiceInfo | None = None
        try:
            self._sp = self._client.Dispatch("SAPI.SpVoice")
            self._tokens = self._enumerate()
        except Exception:
            self.close()
            raise
        log.info("SAPI : %d voix trouvée(s) : %s", len(self._tokens),
                 ", ".join(info.description for info, _ in self._tokens) or "aucune")

    # -- enumeration ---------------------------------------------------------------------

    @staticmethod
    def _attr(token: Any, name: str) -> str | None:
        try:
            value = token.GetAttribute(name)
        except Exception:
            return None
        return value if isinstance(value, str) else None

    def _onecore_tokens(self) -> Any:
        try:
            cat = self._client.Dispatch("SAPI.SpObjectTokenCategory")
            cat.SetId(ONECORE_VOICES_KEY, False)
            return cat.EnumerateTokens()
        except Exception as exc:
            log.debug("SAPI: no OneCore voices (%s)", exc)
            return None

    def _enumerate(self) -> list[tuple[VoiceInfo, Any]]:
        default_id = ""
        try:
            default_id = str(self._sp.Voice.Id)
        except Exception:
            pass
        try:
            sapi5 = self._sp.GetVoices()
        except Exception as exc:
            log.warning("SAPI: GetVoices() failed: %s", exc)
            sapi5 = None
        out: list[tuple[VoiceInfo, Any]] = []
        seen: set[str] = set()
        for source, tokens in (("sapi5", sapi5), ("onecore", self._onecore_tokens())):
            if tokens is None:
                continue
            try:
                count = int(tokens.Count)
            except Exception:
                continue
            for i in range(min(max(count, 0), 200)):
                try:
                    tok = tokens.Item(i)
                    desc = _clean_text(tok.GetDescription(), VOICE_NAME_MAX_LEN)
                except Exception:
                    continue
                if not desc or desc.casefold() in seen:
                    continue
                seen.add(desc.casefold())
                try:
                    tid = str(tok.Id)
                except Exception:
                    tid = ""
                info = VoiceInfo(
                    description=desc,
                    languages=parse_language_ids(self._attr(tok, "Language")),
                    source=source,
                    token_id=tid,
                    is_default=bool(tid) and tid == default_id,
                )
                out.append((info, tok))
        return out

    def voices(self) -> list[str]:
        return [info.description for info, _ in self._tokens]

    # -- configuration -------------------------------------------------------------------

    def _select_voice(self, wanted: str) -> None:
        usable = [(info, tok) for info, tok in self._tokens
                  if (info.token_id or info.description) not in self._bad]
        for idx in rank_voices([info for info, _ in usable], wanted):
            info, tok = usable[idx]
            try:
                self._sp.Voice = tok
            except Exception as exc:
                log.warning("SAPI : voix « %s » inutilisable (%s)", info.description, exc)
                self._bad.add(info.token_id or info.description)
                continue
            self._current = info
            log.info("Voix sélectionnée : %s", info.description)
            return
        self._current = None
        log.warning("Aucune voix française trouvée : voix par défaut de Windows utilisée. "
                    "Installez une voix française (Paramètres > Heure et langue > Voix).")

    def configure(self, voice_name: str, rate: int, volume: int) -> None:
        wanted = _clean_voice_name(voice_name)
        if wanted != self._wanted:
            self._wanted = wanted
            self._select_voice(wanted)
        vol = _clamp_int(volume, *VOLUME_RANGE, DEFAULT_VOLUME)
        self._sp.Rate = _clamp_int(rate, *RATE_RANGE, DEFAULT_RATE)
        self._sp.Volume = vol
        try:
            danger_beep_path(vol)       # generate the WAV now, not at the first DANGER
        except Exception:
            pass

    # -- speech --------------------------------------------------------------------------

    def speak(self, text: str, purge: bool) -> None:
        flags = SVSF_ASYNC | SVSF_IS_NOT_XML | (SVSF_PURGE_BEFORE_SPEAK if purge else 0)
        try:
            self._sp.Speak(text, flags)
        except Exception as exc:
            cur = self._current
            if cur is None:
                raise
            # e.g. a OneCore voice accepted by SAPI but unable to speak: try the next one once
            log.warning("SAPI : échec avec la voix « %s » (%s), essai d'une autre voix",
                        cur.description, exc)
            self._bad.add(cur.token_id or cur.description)
            self._select_voice(self._wanted or "")
            self._sp.Speak(text, flags)

    def is_speaking(self) -> bool:
        try:
            return not bool(self._sp.WaitUntilDone(0))
        except Exception:
            try:
                return int(self._sp.Status.RunningState) == SRSE_IS_SPEAKING
            except Exception:
                return False

    def purge(self) -> None:
        try:
            if self._sp is not None:
                self._sp.Speak("", SVSF_ASYNC | SVSF_PURGE_BEFORE_SPEAK)
        except Exception as exc:
            log.debug("SAPI purge failed: %s", exc)

    def beep(self, volume: int) -> float:
        try:
            import winsound  # noqa: PLC0415 - Windows only
        except ImportError:
            return 0.0
        path = danger_beep_path(volume)
        if path is None and _clamp_int(volume, *VOLUME_RANGE, DEFAULT_VOLUME) < 5:
            return 0.0                      # muted
        if path is not None:
            try:
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC
                                   | winsound.SND_NODEFAULT)
                return BEEP_DURATION_S + 0.02
            except Exception as exc:
                log.debug("PlaySound failed (%s), using Beep()", exc)
        try:
            for k in range(BEEP_COUNT):
                if k:
                    time.sleep(BEEP_GAP_MS / 1000.0)
                winsound.Beep(BEEP_FREQ_HZ, BEEP_TONE_MS)
        except Exception as exc:
            log.debug("winsound.Beep failed: %s", exc)
        return 0.0

    def pump(self) -> None:
        try:
            self._pythoncom.PumpWaitingMessages()
        except Exception:
            pass

    def close(self) -> None:
        self.purge()
        self._sp = None
        self._tokens = []
        self._current = None
        if self._com_initialized:
            self._com_initialized = False
            try:
                self._pythoncom.CoUninitialize()
            except Exception:
                pass


class _WavBackend(SpeechBackend):
    """Common part of the backends playing WAV files with winsound (:class:`tts_neural.WavPlayer`)."""

    name = "wav"

    def __init__(self, *, _player: Any = None) -> None:
        from treeaicoach.tts_neural import WavPlayer  # noqa: PLC0415

        self._player = _player if _player is not None else WavPlayer()
        self._volume = DEFAULT_VOLUME

    def _play(self, path: Path) -> bool:
        return bool(self._player.play(path, self._volume))

    def is_speaking(self) -> bool:
        return bool(self._player.is_playing())

    def purge(self) -> None:
        self._player.stop()

    def beep(self, volume: int) -> float:
        path = danger_beep_path(volume)
        if path is None:
            return 0.0
        try:
            if self._player.play(path, 100):     # the beep WAV is already scaled
                return BEEP_DURATION_S + 0.02
        except Exception as exc:
            log.debug("Beep playback failed: %s", exc)
        return 0.0

    def close(self) -> None:
        self.purge()


class OneCoreBackend(_WavBackend):
    """Windows 10/11 OneCore voices through WinRT (pywinrt); sentences cached as WAV."""

    name = "onecore"

    def __init__(self, *, _synth: Any = None, _player: Any = None, cache_root: Path | None = None) -> None:
        super().__init__(_player=_player)
        from treeaicoach import tts_neural  # noqa: PLC0415

        self._synth = _synth if _synth is not None else tts_neural.OneCoreSynth()
        self._root = Path(cache_root) if cache_root is not None else tts_neural.tts_cache_root()
        self._rate = DEFAULT_RATE
        self._wanted: str | None = None
        log.info("Voix Windows OneCore : %s", getattr(self._synth, "voice_name", "") or "par défaut")

    def voices(self) -> list[str]:
        try:
            return list(self._synth.voices())
        except Exception:
            return []

    def configure(self, voice_name: str, rate: int, volume: int) -> None:
        wanted = _clean_voice_name(voice_name)
        if wanted != self._wanted:
            self._wanted = wanted
            self._synth.select(wanted)
        self._rate = _clamp_int(rate, *RATE_RANGE, DEFAULT_RATE)
        self._volume = _clamp_int(volume, *VOLUME_RANGE, DEFAULT_VOLUME)
        self._synth.configure(self._rate, 100)          # volume applied on the samples
        try:
            danger_beep_path(self._volume)
        except Exception:
            pass

    def speak(self, text: str, purge: bool) -> None:
        import hashlib  # noqa: PLC0415

        if purge:
            self.purge()
        vname = re.sub(r"[^A-Za-z0-9_-]+", "_", str(getattr(self._synth, "voice_name", "") or "default"))[:60]
        key = hashlib.sha1(f"{vname}|{self._rate}|{text}".encode()).hexdigest()
        path = self._root / f"onecore-{vname}" / f"{key}.wav"
        if not (path.is_file() and path.stat().st_size > 100):
            data = self._synth.wav(text)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = Path(f"{path}.{os.getpid()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        if not self._play(path):
            raise RuntimeError("OneCore: playback failed")


class NeuralBackend(_WavBackend):
    """Microsoft Edge neural voice (online, cached) with a local backend for the misses.

    A cached sentence plays instantly. Otherwise it is synthesised for at most
    ``tts_neural.LIVE_TIMEOUT_S`` (0.9 s for a DANGER); on timeout / no network the local
    backend says it (and the synthesis continues in the background to fill the cache).
    """

    name = "neural"

    def __init__(self, neural_voice: str = "", neural_rate: str = "", *, _tts: Any = None,
                 _player: Any = None,
                 _local_factory: Callable[[], SpeechBackend] | None = None) -> None:
        super().__init__(_player=_player)
        from treeaicoach import tts_neural  # noqa: PLC0415

        self._tn = tts_neural
        if _tts is None and not tts_neural.neural_available():
            raise RuntimeError("edge-tts / miniaudio absents")
        self._tts = _tts if _tts is not None else tts_neural.NeuralTTS(
            tts_neural.clean_voice_id(neural_voice), neural_rate or tts_neural.DEFAULT_NEURAL_RATE)
        self._neural_rate = neural_rate or tts_neural.DEFAULT_NEURAL_RATE
        self._phrases: tuple[str, ...] = ()
        self._local_factory = _local_factory or (lambda: _local_backend("sapi"))
        self._local: SpeechBackend | None = None
        self._local_cfg: tuple[str, int, int] | None = None
        self._roster: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())
        self._configured = False
        log.info("Voix naturelle : %s (en ligne, mise en cache)", getattr(self._tts, "voice", neural_voice))

    def _get_local(self) -> SpeechBackend:
        if self._local is None:
            try:
                self._local = self._local_factory()
            except Exception as exc:
                log.warning("Voix Windows de secours indisponible : %s", exc)
                self._local = PrintBackend()
            if self._local_cfg is not None:
                try:
                    self._local.configure(*self._local_cfg)
                except Exception as exc:
                    log.debug("Local voice configure failed: %s", exc)
        return self._local

    def voices(self) -> list[str]:
        try:
            return list(self._get_local().voices())
        except Exception:
            return []

    def configure(self, voice_name: str, rate: int, volume: int) -> None:
        self._volume = _clamp_int(volume, *VOLUME_RANGE, DEFAULT_VOLUME)
        self._local_cfg = (voice_name, rate, volume)
        old = getattr(self._tts, "rate_pct", None)
        self._tts.set_params(getattr(self._tts, "voice", ""), self._neural_rate)
        if self._local is not None:
            self._local.configure(voice_name, rate, volume)
        if not self._configured or getattr(self._tts, "rate_pct", None) != old:
            self._configured = True
            self._prefetch()
        try:
            danger_beep_path(self._volume)
        except Exception:
            pass

    def set_neural_rate(self, rate: str) -> None:
        """Neural voice speed (``"+15%"``); the cache key changes, so pre-generate again."""
        rate = rate or self._tn.DEFAULT_NEURAL_RATE
        if rate == self._neural_rate:
            return
        self._neural_rate = rate
        old = getattr(self._tts, "rate_pct", None)
        self._tts.set_params(getattr(self._tts, "voice", ""), rate)
        if self._configured and getattr(self._tts, "rate_pct", None) != old:
            self._prefetch()

    def set_roster(self, enemies: Sequence[str], allies: Sequence[str] = ()) -> None:
        roster = (tuple(enemies), tuple(allies))
        if roster != self._roster:
            self._roster = roster
            self._prefetch()

    def prewarm(self, phrases: Sequence[str]) -> None:
        """Explicit list of sentences to pre-generate (replaces the roster-derived list)."""
        phrases = tuple(p for p in phrases if isinstance(p, str) and p)
        if phrases != self._phrases:
            self._phrases = phrases
            self._prefetch()

    def _prefetch(self) -> None:
        try:
            if self._phrases:
                texts = list(self._phrases) + self._tn.static_phrases()
            else:
                texts = self._tn.roster_phrases(*self._roster) + self._tn.static_phrases()
            self._tts.prefetch(texts)
        except Exception as exc:
            log.debug("Neural prefetch failed: %s", exc)

    #: set by the voice worker: True when an urgent message waits (stops a network wait)
    abort_check: Callable[[], bool] | None = None

    def speak(self, text: str, purge: bool) -> None:
        if purge:
            self.purge()
        timeout = 0.9 if purge else self._tn.LIVE_TIMEOUT_S
        path = None
        try:
            path = self._tts.get(text, timeout, abort=self.abort_check)
        except TypeError:                     # test doubles without ``abort``
            path = self._tts.get(text, timeout)
        except Exception as exc:
            log.debug("Neural synthesis failed: %s", exc)
        if path is None and self.abort_check is not None and self.abort_check():
            return                            # superseded by an urgent message: not said
        if path is not None and self._play(Path(path)):
            return
        self._get_local().speak(text, purge)

    def speak_urgent(self, text: str, purge: bool) -> None:
        """Gank alerts: the cached neural WAV, else the local voice RIGHT NOW (no network wait;
        the synthesis runs in the background so the next one is cached)."""
        if purge:
            self.purge()
        path = None
        try:
            path = self._tts.get(text, 0.0)
        except Exception as exc:
            log.debug("Neural cache lookup failed: %s", exc)
        if path is not None and self._play(Path(path)):
            return
        self._get_local().speak(text, purge)

    def is_speaking(self) -> bool:
        if self._player.is_playing():
            return True
        local = self._local
        return bool(local is not None and local.is_speaking())

    def purge(self) -> None:
        self._player.stop()
        if self._local is not None:
            try:
                self._local.purge()
            except Exception:
                pass

    def pump(self) -> None:
        if self._local is not None:
            self._local.pump()

    def close(self) -> None:
        self.purge()
        if self._local is not None:
            try:
                self._local.close()
            except Exception:
                pass
            self._local = None


ENGINES: tuple[str, ...] = ("auto", "neural", "onecore", "sapi")
ENGINE_LABELS: dict[str, str] = {
    "auto": "Automatique (voix naturelle, sinon Windows)",
    "neural": "Voix naturelle Microsoft (en ligne)",
    "onecore": "Voix Windows 10/11 (hors ligne)",
    "sapi": "Voix Windows classique (SAPI)",
}


def _coerce_engine(value: Any) -> str:
    s = value.strip().lower() if isinstance(value, str) else ""
    return s if s in ENGINES else "auto"


def _local_backend(engine: str = "onecore") -> SpeechBackend:
    """OneCore (if asked) -> SAPI -> print. Never raises."""
    if sys.platform != "win32":
        return PrintBackend()
    if engine == "onecore":
        try:
            return OneCoreBackend()
        except Exception as exc:
            log.info("Voix Windows OneCore indisponible (%s) : essai de SAPI.", exc)
    try:
        return SapiBackend()
    except Exception as exc:
        log.warning("Synthèse vocale Windows (SAPI) indisponible (%s) : "
                    "les annonces seront seulement écrites dans le journal.", exc)
    return PrintBackend()


def make_backend(engine: str = "auto", neural_voice: str = "", neural_rate: str = "") -> SpeechBackend:
    """Backend chain for ``engine``: neural -> sapi -> print (``onecore`` -> sapi -> print).
    Never raises."""
    engine = _coerce_engine(engine)
    if sys.platform != "win32":
        return PrintBackend()
    if engine in ("auto", "neural"):
        try:
            return NeuralBackend(neural_voice, neural_rate)
        except Exception as exc:
            log.warning("Voix naturelle indisponible (%s) : voix Windows utilisée.", exc)
    return _local_backend("onecore" if engine == "onecore" else "sapi")


def _default_backend_factory() -> SpeechBackend:
    """Default chain (auto)."""
    return make_backend("auto")


# --------------------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Params:
    voice_name: str
    rate: int
    volume: int
    beep_on_danger: bool
    engine: str = "auto"
    neural_voice: str = ""
    neural_rate: str = "+15%"


@dataclass(frozen=True)
class _Item:
    text: str
    level: int
    t: float          # enqueue time (engine clock)
    seq: int


class VoiceEngine:
    """Non-blocking French voice. ``say()`` may be called from any thread."""

    def __init__(self, voice_name: str = "", rate: int = DEFAULT_RATE, volume: int = DEFAULT_VOLUME,
                 beep_on_danger: bool = True, engine: str = "auto", neural_voice: str = "",
                 neural_rate: str = "+15%", *,
                 _backend_factory: Callable[[], SpeechBackend] | None = None,
                 _clock: Callable[[], float] = time.monotonic,
                 _max_age_s: float = MAX_AGE_S) -> None:
        self._factory: Callable[[], SpeechBackend] = _backend_factory or _default_backend_factory
        self._custom_factory = _backend_factory is not None
        self._clock = _clock
        self._max_age_s = float(_max_age_s)
        self._cond = threading.Condition(threading.Lock())
        self._queue: deque[_Item] = deque()
        self._seq = 0
        self._params = _Params(
            voice_name=_clean_voice_name(voice_name),
            rate=_clamp_int(rate, *RATE_RANGE, DEFAULT_RATE),
            volume=_clamp_int(volume, *VOLUME_RANGE, DEFAULT_VOLUME),
            beep_on_danger=bool(beep_on_danger),
            engine=_coerce_engine(engine),
            neural_voice=_clean_voice_name(neural_voice),
            neural_rate=_coerce_neural_rate(neural_rate, "+15%"),
        )
        self._params_version = 0
        self._phrases: tuple[str, ...] = ()
        self._roster: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())
        self._roster_version = 0
        self._purge_requests = 0
        self._thread: threading.Thread | None = None
        self._token: threading.Event | None = None      # stop token of the current worker
        self._closed = False
        self._muted = False
        self._busy = False
        self._ready = threading.Event()
        self._backend_name: str | None = None
        self._voices_cache: list[str] | None = None
        self.spoken_count = 0          # statistics (read-only for callers)
        self.dropped_count = 0

    # -- public API -----------------------------------------------------------------------

    @property
    def backend(self) -> str:
        """``"neural"``, ``"onecore"``, ``"sapi"`` or ``"print"`` (expected one until started)."""
        name = self._backend_name
        if name:
            return name
        if not self._custom_factory and sys.platform == "win32":
            eng = self._params.engine
            return "neural" if eng == "auto" else eng
        return "print"

    def prefetch(self, enemies: Sequence[str], allies: Sequence[str] = ()) -> None:
        """Champions of the game (localised names): the neural voice pre-generates every
        sentence the coach may say about them, so a gank alert plays without network latency.
        Thread-safe, non-blocking, never raises."""
        try:
            def clean(names: Any) -> tuple[str, ...]:
                out = []
                for n in list(names or ())[:10]:
                    c = _clean_text(n, 40)
                    if c and c not in out:
                        out.append(c)
                return tuple(out)

            roster = (clean(enemies), clean(allies))
            with self._cond:
                if roster != self._roster:
                    self._roster = roster
                    self._roster_version += 1
                    self._cond.notify_all()
        except Exception:
            log.exception("VoiceEngine.prefetch failed")

    def prewarm(self, phrases: Sequence[str]) -> None:
        """Sentences to pre-generate with the neural voice (``tts_neural.build_phrase_list(game)``
        at game start) so they play instantly. Thread-safe, non-blocking, never raises."""
        try:
            out: list[str] = []
            seen: set[str] = set()
            for p in list(phrases or ())[:1000]:
                c = _clean_text(p)
                if c and c not in seen:
                    seen.add(c)
                    out.append(c)
            phrases_t = tuple(out)
            with self._cond:
                if phrases_t != self._phrases:
                    self._phrases = phrases_t
                    self._roster_version += 1
                    self._cond.notify_all()
        except Exception:
            log.exception("VoiceEngine.prewarm failed")

    @staticmethod
    def list_engines() -> list[tuple[str, str]]:
        """``[(engine id, French label), ...]`` for the settings (``cfg.voice_engine``)."""
        return [(k, ENGINE_LABELS[k]) for k in ENGINES]

    @staticmethod
    def list_neural_voices() -> list[tuple[str, str]]:
        """``[(neural voice id, French label), ...]`` for the settings (``cfg.neural_voice``)."""
        try:
            from treeaicoach.tts_neural import list_neural_voices  # noqa: PLC0415

            return list_neural_voices()
        except Exception:
            return [("fr-FR-DeniseNeural", "Denise (femme, France)")]

    def start(self) -> None:
        """Start the voice thread (idempotent, returns immediately)."""
        try:
            with self._cond:
                self._closed = False
                th = self._thread
                if th is not None and th.is_alive() and self._token is not None \
                        and not self._token.is_set():
                    return
                token = threading.Event()
                self._token = token
                self._ready.clear()
                th = threading.Thread(target=self._run, args=(token,), name="TreeAICoach-voice",
                                      daemon=True)
                self._thread = th
            th.start()
        except Exception:
            log.exception("Cannot start the voice thread")

    def stop(self, timeout: float = STOP_TIMEOUT_S) -> None:
        """Stop the voice (current sentence cut, queue cleared) and join the thread."""
        with self._cond:
            self._closed = True
            th, token = self._thread, self._token
            if token is not None:
                token.set()
            self._queue.clear()
            self._cond.notify_all()
        if th is not None and th is not threading.current_thread():
            wait_s = _finite_nonneg(timeout, STOP_TIMEOUT_S)
            th.join(wait_s)
            if th.is_alive():
                log.warning("The voice thread did not stop within %.1f s", wait_s)

    def is_running(self) -> bool:
        th = self._thread
        return th is not None and th.is_alive() and not self._closed

    def say(self, text: str, level: int = LEVEL_WARNING) -> None:
        """Queue ``text`` (never blocks, never raises). Level 2 (DANGER) cuts the current sentence."""
        try:
            s = _clean_text(text)
            if not s:
                return
            lvl = _coerce_level(level)
            with self._cond:
                if self._closed:
                    log.debug("Voice stopped, message ignored: %s", s)
                    return
                if self._muted:
                    return
                self._seq += 1
                item = _Item(s, lvl, float(self._clock()), self._seq)
                if lvl >= LEVEL_DANGER:
                    self.dropped_count += len(self._queue)
                    self._queue.clear()
                    self._queue.append(item)
                else:
                    # a WARNING (gank) goes before the less urgent messages already queued
                    i = len(self._queue)
                    while i > 0 and self._queue[i - 1].level < lvl:
                        i -= 1
                    self._queue.insert(i, item)
                    while len(self._queue) > MAX_QUEUE:
                        self._drop_oldest_locked()
                self._cond.notify_all()
                th = self._thread
                need_start = th is None or not th.is_alive()
            if need_start:
                self.start()
        except Exception:
            log.exception("VoiceEngine.say failed")

    def list_voices(self) -> list[str]:
        """Descriptions of the installed voices (empty off Windows). Never raises."""
        cache = self._voices_cache
        if cache is not None:
            return list(cache)
        if self._custom_factory or sys.platform != "win32":
            return []
        result: list[str] = []
        done = threading.Event()

        def job() -> None:
            backend: SpeechBackend | None = None
            try:
                backend = SapiBackend()
                result.extend(backend.voices())
                done.set()
            except Exception as exc:
                log.warning("Impossible de lister les voix Windows : %s", exc)
            finally:
                if backend is not None:
                    backend.close()

        th = threading.Thread(target=job, name="TreeAICoach-voices", daemon=True)
        th.start()
        th.join(LIST_VOICES_TIMEOUT_S)
        if done.is_set():
            self._voices_cache = list(result)
            return list(result)
        return []

    def set_params(self, voice_name: str | None = None, rate: int | None = None,
                   volume: int | None = None, beep_on_danger: bool | None = None,
                   engine: str | None = None, neural_voice: str | None = None,
                   neural_rate: str | None = None) -> None:
        """Change the voice settings (applied live by the voice thread). Never raises."""
        try:
            with self._cond:
                p = self._params
                new = _Params(
                    voice_name=p.voice_name if voice_name is None else _clean_voice_name(voice_name),
                    rate=p.rate if rate is None else _clamp_int(rate, *RATE_RANGE, p.rate),
                    volume=p.volume if volume is None else _clamp_int(volume, *VOLUME_RANGE, p.volume),
                    beep_on_danger=p.beep_on_danger if beep_on_danger is None else bool(beep_on_danger),
                    engine=p.engine if engine is None else _coerce_engine(engine),
                    neural_voice=p.neural_voice if neural_voice is None else _clean_voice_name(neural_voice),
                    neural_rate=p.neural_rate if neural_rate is None
                    else _coerce_neural_rate(neural_rate, p.neural_rate),
                )
                if new != p:
                    self._params = new
                    self._params_version += 1
                    self._cond.notify_all()
        except Exception:
            log.exception("VoiceEngine.set_params failed")

    @property
    def muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        """Mute (queue cleared, current sentence cut) / unmute."""
        with self._cond:
            self._muted = bool(muted)
            if self._muted:
                self._queue.clear()
                self._purge_requests += 1
                self._cond.notify_all()

    def wait_ready(self, timeout: float = 3.0) -> bool:
        """Wait until the voice thread has created its backend (tests / selftest)."""
        return self._ready.wait(_finite_nonneg(timeout, 3.0))

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Wait until the queue is empty and nothing is being spoken (tests / selftest)."""
        deadline = time.monotonic() + _finite_nonneg(timeout, 5.0)
        while True:
            with self._cond:
                if not self._queue and not self._busy:
                    return True
                th = self._thread
                if th is None or not th.is_alive():
                    return not self._queue
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

    # -- queue internals (lock held) ---------------------------------------------------

    def _drop_oldest_locked(self) -> None:
        for i, it in enumerate(self._queue):
            if it.level < LEVEL_DANGER:
                del self._queue[i]
                break
        else:
            self._queue.popleft()
        self.dropped_count += 1

    def _is_stale(self, item: _Item) -> bool:
        return float(self._clock()) - item.t > self._max_age_s

    def _pop_locked(self) -> _Item | None:
        while self._queue:
            item = self._queue.popleft()
            if self._is_stale(item):
                self.dropped_count += 1
                log.debug("Voice: stale message dropped: %s", item.text)
                continue
            self._busy = True
            return item
        return None

    def _count_drop(self) -> None:
        with self._cond:
            self.dropped_count += 1

    def _count_spoken(self) -> None:
        with self._cond:
            self.spoken_count += 1

    def _danger_queued(self) -> bool:
        with self._cond:
            return any(it.level >= LEVEL_DANGER for it in self._queue)

    def _urgent_queued(self, above: int = LEVEL_INFO) -> bool:
        """A message of level > ``above`` and >= WARNING waits in the queue."""
        with self._cond:
            return any(it.level > above and it.level >= LEVEL_WARNING for it in self._queue)

    # -- worker -------------------------------------------------------------------------

    def _run(self, token: threading.Event) -> None:
        _Worker(self, token).run()


class _Worker:
    """The voice thread body (one instance per started thread)."""

    def __init__(self, engine: VoiceEngine, token: threading.Event) -> None:
        self.e = engine
        self.token = token
        self.backend: SpeechBackend = PrintBackend()
        self.applied_version = -1
        self.purges_seen = 0
        self.failures = 0
        self.started_at: float | None = None      # start of the current sentence (engine clock)
        self.backend_key: tuple[str, str] | None = None   # (engine, neural voice) of the backend
        self.roster_seen = -1
        self._last_error_log = -math.inf
        self.cur_level = LEVEL_INFO               # level of the sentence being spoken
        self.item_level = LEVEL_INFO              # level of the sentence being synthesised

    # -- setup ------------------------------------------------------------------------

    def _create_backend(self) -> SpeechBackend:
        p = self.e._params
        self.backend_key = (p.engine, p.neural_voice)
        try:
            if self.e._custom_factory:
                backend = self.e._factory()
            else:
                backend = make_backend(p.engine, p.neural_voice, p.neural_rate)
            if backend is None:
                raise RuntimeError("backend factory returned None")
            return backend
        except Exception as exc:
            log.warning("Voix indisponible (%s) : les annonces seront écrites dans le journal.", exc)
            return PrintBackend()

    def _install(self, backend: SpeechBackend) -> None:
        self.backend = backend
        if hasattr(backend, "abort_check"):
            try:     # a network wait for an INFO sentence stops as soon as a gank alert waits
                backend.abort_check = lambda: self.e._urgent_queued(self.item_level)  # type: ignore[attr-defined]
            except Exception:
                pass
        self.applied_version = -1
        self.roster_seen = -1
        self.started_at = None
        self.e._backend_name = str(getattr(backend, "name", "print") or "print")
        try:
            self.e._voices_cache = list(backend.voices())
        except Exception:
            self.e._voices_cache = []

    def _fallback_to_print(self, reason: str) -> None:
        log.error("La synthèse vocale ne fonctionne plus (%s) : les annonces seront écrites "
                  "dans le journal.", reason)
        self._close_backend()
        self._install(PrintBackend())

    def _close_backend(self) -> None:
        try:
            self.backend.close()
        except Exception as exc:
            log.debug("Voice backend close failed: %s", exc)

    # -- loop ---------------------------------------------------------------------------

    def run(self) -> None:
        try:
            self._install(self._create_backend())
        finally:
            self.e._ready.set()
        try:
            while not self.token.is_set():
                try:
                    self._step()
                except Exception:
                    self._log_error()
                    self.token.wait(0.2)        # no hot loop on a persistent error
        finally:
            try:
                self.backend.purge()
            except Exception:
                pass
            self._close_backend()
            with self.e._cond:
                self.e._busy = False

    def _log_error(self) -> None:
        now = time.monotonic()
        if now - self._last_error_log >= ERROR_LOG_EVERY_S:
            self._last_error_log = now
            log.exception("Voice thread error (continuing)")

    def _speaking(self) -> bool:
        try:
            speaking = bool(self.backend.is_speaking())
        except Exception:
            speaking = False
        if not speaking:
            self.started_at = None
        elif self.started_at is not None and float(self.e._clock()) - self.started_at > MAX_UTTERANCE_S:
            log.warning("Voice: sentence stuck for more than %.0f s, cut", MAX_UTTERANCE_S)
            self._purge()
            speaking = False
        return speaking

    def _purge(self) -> None:
        try:
            self.backend.purge()
        except Exception as exc:
            log.debug("Voice purge failed: %s", exc)
        self.started_at = None

    def _apply_params(self) -> _Params:
        e = self.e
        with e._cond:
            params, version, purges = e._params, e._params_version, e._purge_requests
        if purges != self.purges_seen:
            self.purges_seen = purges
            self._purge()
        if not e._custom_factory and self.backend_key is not None \
                and self.backend_key != (params.engine, params.neural_voice):
            log.info("Moteur de voix : %s", params.engine)
            self._purge()
            self._close_backend()
            self._install(self._create_backend())
        if version != self.applied_version:
            self.applied_version = version
            set_rate = getattr(self.backend, "set_neural_rate", None)
            if callable(set_rate):
                try:
                    set_rate(params.neural_rate)
                except Exception as exc:
                    log.debug("Neural rate not applied: %s", exc)
            try:
                self.backend.configure(params.voice_name, params.rate, params.volume)
            except Exception as exc:
                log.warning("Réglages de la voix non appliqués : %s", exc)
            try:
                e._voices_cache = list(self.backend.voices())
            except Exception:
                pass
        if e._roster_version != self.roster_seen:
            self.roster_seen = e._roster_version
            set_roster = getattr(self.backend, "set_roster", None)
            if callable(set_roster):
                try:
                    set_roster(*e._roster)
                except Exception as exc:
                    log.debug("Voice set_roster failed: %s", exc)
            prewarm = getattr(self.backend, "prewarm", None)
            if callable(prewarm) and e._phrases:
                try:
                    prewarm(e._phrases)
                except Exception as exc:
                    log.debug("Voice prewarm failed: %s", exc)
        return params

    def _next(self) -> _Item | None:
        """Pop the next fresh message, or wait a little and return None."""
        e = self.e
        speaking = self._speaking()
        with e._cond:
            e._busy = speaking
            item = e._pop_locked()
            if item is not None or self.token.is_set():
                return item
            e._cond.wait(POLL_S if speaking else IDLE_WAIT_S)
        return None

    def _step(self) -> None:
        params = self._apply_params()
        item = self._next()
        if item is None:
            self.backend.pump()
            return
        if item.level >= LEVEL_DANGER:
            self._say_danger(item, params)
        else:
            self._say_normal(item)

    def _wait(self, seconds: float) -> None:
        """Interruptible short wait (stop token)."""
        if seconds > 0:
            self.token.wait(min(seconds, 1.0))

    def _say_normal(self, item: _Item) -> None:
        e = self.e
        if item.level >= LEVEL_WARNING and item.level > self.cur_level and self._speaking():
            self._speak(item, purge=True)       # a gank warning cuts a less urgent sentence
            return
        while self._speaking():
            if self.token.is_set():
                return
            if e._danger_queued():
                e._count_drop()                 # superseded by a DANGER queued after it
                return
            if e._is_stale(item):
                break
            self.backend.pump()
            with e._cond:
                e._cond.wait(POLL_S)
            self._apply_params()
        if self.token.is_set():
            return
        if e._is_stale(item):
            e._count_drop()
            log.debug("Voice: stale message dropped: %s", item.text)
            return
        self._speak(item, purge=False)

    def _say_danger(self, item: _Item, params: _Params) -> None:
        if params.beep_on_danger:
            self._purge()
            try:
                delay = float(self.backend.beep(params.volume) or 0.0)
            except Exception as exc:
                log.debug("Beep failed: %s", exc)
                delay = 0.0
            self._wait(delay)
            if self.token.is_set():
                return
            if self.e._danger_queued():         # a newer DANGER arrived during the beep
                self.e._count_drop()
                return
        self._speak(item, purge=True)

    def _speak(self, item: _Item, purge: bool) -> None:
        self.item_level = item.level
        for attempt in range(2):
            try:
                urgent = getattr(self.backend, "speak_urgent", None) if item.level >= LEVEL_WARNING else None
                if callable(urgent):
                    urgent(item.text, purge)
                else:
                    self.backend.speak(item.text, purge)
            except Exception as exc:
                self.failures += 1
                log.warning("Échec de la synthèse vocale (%s) : %s", exc, item.text)
                if self.backend.name == "print":
                    return
                if self.failures >= MAX_SPEAK_FAILURES:
                    self._fallback_to_print(str(exc))
                    self.backend.speak(item.text, purge)
                    return
                if attempt == 0:
                    # the COM object may be dead (audio device change...): recreate it once
                    self._close_backend()
                    self._install(self._create_backend())
                    self._apply_params()
                continue
            self.failures = 0
            self.started_at = float(self.e._clock())
            self.cur_level = item.level
            self.e._count_spoken()
            return
