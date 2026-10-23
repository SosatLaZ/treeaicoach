"""Voice assistant « Hey coach » (off by default): the player says the wake phrase, then a question;
the coach answers out loud through the existing AI (manual question path, counted in the per-game cap).

Offline and light:

* speech recognition = Vosk (Kaldi, ``vosk`` wheel, libvosk.dll bundled) with the small French model
  (``vosk-model-small-fr-0.22``, ~41 MB zip) downloaded ONCE into ``paths.cache_dir()/voice`` the first
  time the assistant is switched on (never bundled in the exe);
* microphone = ``sounddevice`` (PortAudio wheel), 16 kHz mono int16, 0.25 s blocks;
* only a grammar-limited recognizer runs continuously (the wake phrase variants: cheap); the free
  recognizer runs only for the question (<= :data:`QUESTION_MAX_S`, ends on silence);
* the microphone is CLOSED when not in game (unless a test is running): zero CPU between games;
* blocks heard while the app's own voice is speaking are dropped (no self-trigger);
* push-to-talk: a hotkey (``hotkey_voice_ask``) skips the wake phrase and listens to the question.

The French model hears "Hey coach" as « hé coach », « et coach », « hey coche »…: :func:`is_wake` accepts
these variants (accents and case ignored), the grammar carries decoys (« le coach »…) and :class:`WakeDetector`
confirms a wake heard in partial results over 2 blocks. Everything is injectable (mic, recognizer) for the tests.
Nothing here ever raises into the caller."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import unicodedata
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
BLOCK_S = 0.25
BLOCK_FRAMES = int(SAMPLE_RATE * BLOCK_S)
QUESTION_MAX_S = 6.0            # the question is cut after this
QUESTION_SILENCE_S = 1.2        # ... or ends after this much silence once speech was heard
QUESTION_START_S = 3.0          # nothing said within this after the wake chime: give up
TEST_WINDOW_S = 20.0            # "Tester": listen this long even outside a game
IDLE_POLL_S = 0.5               # paused: how often the gate is re-checked (mic closed)
MIN_QUESTION_WORDS = 2
SPEAKING_MAX_S = 15.0           # the app's voice "busy" longer than this: the gate is stuck, ignored
CHIME_S = 0.8                   # the wake chime: the question's first words are not dropped for it
SILENT_MIC_RMS = 4.0            # a block this quiet is digital silence (muted / wrong device)
SILENT_MIC_BLOCKS = 40          # ... for 10 s: reported in the panel
STAGE_LABELS = {"off": "désactivé", "loading": "chargement du modèle", "ready": "modèle prêt",
                "listening": "écoute", "wake": "mot-clé entendu", "question": "question",
                "answer": "réponse", "error": "erreur"}

#: The Vosk engine (libvosk.dll, ~17 MB compressed) is NOT bundled in the exe (GitHub's 100 MB file limit):
#: its official Windows wheel is downloaded once next to the model and checked by SHA-256.
VOSK_WHEEL_URL = ("https://files.pythonhosted.org/packages/c0/4c/deb0861f7da9696f8a255f1731bb73e9412cca29c4b3888a3fcb2a930a59/"
                  "vosk-0.3.45-py3-none-win_amd64.whl")
VOSK_WHEEL_SHA256 = "6994ddc68556c7e5730c3b6f6bad13320e3519b13ce3ed2aa25a86724e7c10ac"
MODEL_NAME = "vosk-model-small-fr-0.22"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
MODEL_SIZE_MB = 41

#: grammar of the always-on recognizer: what the French model may hear for "Hey coach" ("[unk]"
#: absorbs everything else, so ordinary talk costs nothing and never matches)
WAKE_GRAMMAR: tuple[str, ...] = (
    "hey coach", "hé coach", "et coach", "eh coach", "hey coche", "hé coche", "et coche", "eh coche",
    "hey coq", "hé coq",
    # decoys (never a wake): ordinary sentences about "le coach" must not be forced into « hé coach »
    "le coach", "ce coach", "du coach", "mon coach", "ton coach", "au coach", "de coach",
    "le coche", "les coachs", "et toi", "et tu", "et si", "et on", "et les", "et le", "et là", "[unk]",
)
_WAKE_RE = re.compile(r"\b(?:hey|hei|he|eh|et|ey|hay|ai)\s+(?:coach|coache|coaches|coche|coatch|cotch|couche|cauche|koch|coq)\b")


def normalize(text: Any) -> str:
    """Lower case, no accents, single spaces."""
    s = unicodedata.normalize("NFD", str(text or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", s).split())


def is_wake(text: Any) -> bool:
    """True when ``text`` (recognizer output) is a variant of « Hey coach »."""
    return bool(_WAKE_RE.search(normalize(text)))


#: wake words: a lead (« hé », « hey », « et »…) then a coach word. STRONG = « coach » itself (what the
#: model writes for the real phrase); WEAK = what it writes for other accents (« hé coche ») but also hears
#: in ordinary talk (« il faut cocher ») -> accepted only when said alone (final result, nothing after).
#: « coq » is never a wake (« Hé, quelqu'un… » is heard « hé coq ») : it stays in the grammar as a decoy.
WAKE_LEADS = frozenset({"hey", "hei", "he", "eh", "et", "ey", "hay", "ai", "e"})
WAKE_STRONG = frozenset({"coach", "coache", "coaches", "coatch", "cotch"})
WAKE_WEAK = frozenset({"coche", "couche", "cauche", "koch"})
WEAK_MIN_CONF = 0.6             # « hé coche » alone: both words at least this sure
BARE_MIN_CONF = 0.7             # « Coach, … » alone at the start: the word's confidence in a final result
QUIET_BLOCKS = 2                # « Coach » alone counts only after this many quiet blocks (0.5 s of silence)


def wake_kind(text: Any, confs: Sequence[float] | None = None, final: bool = False) -> str:
    """How ``text`` (recognizer output, ``[unk]`` kept or not) calls the coach: ``"strong"`` (« hé coach »
    anywhere), ``"weak"`` (« hé coche » said alone, final only), ``"bare"`` (« coach » as the FIRST word;
    in a final result its confidence must reach :data:`BARE_MIN_CONF`) or ``""``. Never raises."""
    try:
        t = normalize(text).split()
        for i in range(1, len(t)):
            if t[i] in WAKE_STRONG and t[i - 1] in WAKE_LEADS:
                return "strong"
        real = [w for w in t if w != "unk"]
        if final and len(real) == 2 and t[:2] == real and real[0] in WAKE_LEADS and real[1] in WAKE_WEAK \
                and (not confs or min(float(c) for c in confs[:2]) >= WEAK_MIN_CONF):
            return "weak"
        if t and t[0] in WAKE_STRONG:
            c = confs[0] if confs else None
            if not final or c is None or float(c) >= BARE_MIN_CONF:
                return "bare"
    except Exception:
        pass
    return ""


def block_rms(pcm: bytes) -> float:
    """RMS of an int16 block (0..32768). Never raises (0 on bad input)."""
    try:
        import numpy as np  # noqa: PLC0415

        a = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32)
        return float(np.sqrt(np.mean(a * a))) if a.size else 0.0
    except Exception:
        return 0.0


def level_of(rms: float) -> float:
    """Mic level for a meter, 0..1 (-60 dBFS -> 0, -6 dBFS -> 1)."""
    try:
        import math  # noqa: PLC0415

        db = 20.0 * math.log10(max(float(rms), 1.0) / 32768.0)
        return max(0.0, min(1.0, (db + 60.0) / 54.0))
    except Exception:
        return 0.0


def level_bar(level: float, n: int = 8) -> str:
    """``"▮▮▮▯▯▯▯▯"`` for a 0..1 level."""
    k = max(0, min(n, int(round(float(level or 0.0) * n))))
    return "▮" * k + "▯" * (n - k)


class WakeDetector:
    """Wake decision over a stream of recognizer outputs (shared by the PC mic and the Discord listener).

    * « hé coach » (strong) in a FINAL result fires at once; in PARTIAL results it must stay
      ``confirm`` (2) consecutive blocks (the grammar-limited decoder flips between « hé coach » and
      ``[unk]`` while ordinary words go by: « Et quoi, on… » shows « eh coach » for one block);
    * « Coach, … » (bare, first word of the utterance) only after :data:`QUIET_BLOCKS` quiet blocks
      (the player starts talking to the coach; a « coach » glued to « ce coach est… » is ignored),
      same 2-block confirmation in partials, confidence >= :data:`BARE_MIN_CONF` in a final;
    * « hé coche » said alone (final result only).

    Measured on 156 edge-tts clips (6 French voices, 8 wake / 18 decoy sentences, real Vosk model):
    47/48 wakes, 0/108 false wakes (1/108 with noise at 5 dB SNR); the old « 2 partials, hé + coach/coche/coq »
    rule: 36/48 (every « Coach, … » missed) and 7/108 false (« Hé, quelqu'un… », « il faut cocher… »).
    ``last_text`` = the last thing heard (for the « Écoute » panel)."""

    PARTIAL_CONFIRM = 2

    def __init__(self, confirm: int | None = None) -> None:
        self.confirm = int(confirm or self.PARTIAL_CONFIRM)
        self._run = 0
        self._quiet = QUIET_BLOCKS          # quiet blocks in a row (starts "after a silence")
        self._after_quiet = True            # this utterance started after a silence
        self._floor: float | None = None     # background level (slow-tracked minimum)
        self.last_text = ""
        self.last_kind = ""
        self.rms = 0.0

    def reset(self) -> None:
        self._run = 0
        self._after_quiet = self._quiet >= QUIET_BLOCKS

    def _energy(self, block: bytes) -> None:
        r = self.rms = block_rms(block)
        self._floor = r if self._floor is None else min(r, self._floor * 1.02 + 1.0)
        if r < max(150.0, 2.5 * self._floor):
            self._quiet += 1
        else:
            if self._quiet >= QUIET_BLOCKS:
                self._after_quiet = True
            self._quiet = 0

    @staticmethod
    def _out(rec: Any, final: bool) -> tuple[str, list[float] | None]:
        """(text with ``[unk]`` when the recognizer keeps it, word confidences of a final result)."""
        txt = rec.result() if final else rec.partial()
        raw = getattr(rec, "last_raw", None)
        confs = getattr(rec, "last_confs", None) if final else None
        return (raw if isinstance(raw, str) and raw.strip() else str(txt or "")), confs

    def feed(self, rec: Any, block: bytes) -> bool:
        try:
            self._energy(block)
            if rec.accept(block):
                text, confs = self._out(rec, True)
                kind = wake_kind(text, confs, final=True)
                self._run = 0
                ok = bool(kind) and (kind != "bare" or self._after_quiet)
                self._after_quiet = self._quiet >= QUIET_BLOCKS
                self._note(text, kind if ok else "")
                return ok
            text, _ = self._out(rec, False)
            kind = wake_kind(text)
            if kind == "bare" and (not self._after_quiet or self._quiet >= QUIET_BLOCKS):
                kind = ""                   # not after a silence / « heard » in the silence itself
            if kind:
                self._run += 1
                self._note(text, kind)
                return self._run >= self.confirm
            if text:
                self._note(text, "")
            self._run = 0
        except Exception:
            self._run = 0
        return False

    def _note(self, text: str, kind: str) -> None:
        t = " ".join(str(text).replace("[unk]", "…").split())
        if t and t != "…":
            self.last_text, self.last_kind = t[:60], kind


def strip_wake(text: Any) -> str:
    """The question without a leading wake phrase (« hé coach quel item » -> « quel item »)."""
    s = str(text or "").strip()
    m = _WAKE_RE.search(normalize(s))
    if m is None:
        return s
    words = s.split()
    n = len(normalize(s)[:m.end()].split())
    while n < len(words) and normalize(words[n]) in ("aie", "ai", "ail"):
        n += 1
    return " ".join(words[n:])


def short_answer(text: Any, sentences: int = 2, max_chars: int = 220) -> str:
    """The first ``sentences`` sentences of ``text`` (bounded): what is read out loud."""
    s = " ".join(str(text or "").split())
    parts = re.split(r"(?<=[.!?…])\s+", s)
    out = " ".join(parts[:max(1, sentences)])
    if len(out) > max_chars:
        cut = out[:max_chars]
        out = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(",;: ") + "…"
    return out


# ------------------------------------------------------------------ pluggable audio / recognizer
class Recognizer(Protocol):
    def accept(self, pcm: bytes) -> bool: ...      # True: an utterance ended (``result`` ready)
    def result(self) -> str: ...
    def partial(self) -> str: ...
    def final(self) -> str: ...
    def reset(self) -> None: ...


class Mic(Protocol):
    def read(self) -> bytes | None: ...            # one BLOCK_S block of int16 mono 16 kHz (None: error)
    def close(self) -> None: ...


class VoskRecognizer:
    """Thin wrapper over ``vosk.KaldiRecognizer`` (text out of its JSON). ``last_raw`` keeps the text
    WITH ``[unk]`` (where ordinary words were: « Coach » must be the FIRST word) and ``last_confs`` the
    word confidences of the last final result."""

    def __init__(self, model: Any, grammar: Sequence[str] | None) -> None:
        vosk = import_vosk()

        if grammar:
            self._r = vosk.KaldiRecognizer(model, SAMPLE_RATE, json.dumps(list(grammar), ensure_ascii=False))
            try:
                self._r.SetWords(True)
            except Exception:
                pass
        else:
            self._r = vosk.KaldiRecognizer(model, SAMPLE_RATE)
        self.last_raw = ""
        self.last_confs: list[float] | None = None

    def _text(self, js: str, key: str) -> str:
        try:
            d = json.loads(js)
            raw = str(d.get(key) or "")
            self.last_raw = raw
            if key == "text":
                self.last_confs = [float(w.get("conf", 1.0)) for w in d.get("result") or []] or None
            return raw.replace("[unk]", " ").strip() and " ".join(raw.replace("[unk]", " ").split())
        except Exception:
            self.last_raw = ""
            return ""

    def accept(self, pcm: bytes) -> bool:
        return bool(self._r.AcceptWaveform(pcm))

    def result(self) -> str:
        return self._text(self._r.Result(), "text")

    def partial(self) -> str:
        return self._text(self._r.PartialResult(), "partial")

    def final(self) -> str:
        return self._text(self._r.FinalResult(), "text")

    def reset(self) -> None:
        self._r.Reset()


def resample_to_16k(pcm: bytes, rate: int, channels: int) -> bytes:
    """int16 interleaved ``channels`` at ``rate`` -> int16 mono 16 kHz (channel mean, linear
    interpolation after a box low-pass). Never raises (b"" on bad input)."""
    try:
        import numpy as np  # noqa: PLC0415

        ch = max(1, int(channels))
        a = np.frombuffer(pcm[: len(pcm) // (2 * ch) * 2 * ch], dtype="<i2").astype(np.float32)
        a = a.reshape(-1, ch).mean(axis=1)
        rate = int(rate)
        if rate == SAMPLE_RATE or a.size == 0:
            return a.astype("<i2").tobytes()
        k = max(1, int(round(rate / SAMPLE_RATE)))
        if k > 1:
            a = np.convolve(a, np.ones(k, np.float32) / k, mode="same")
        n = int(round(a.size * SAMPLE_RATE / rate))
        x = np.linspace(0.0, a.size - 1, num=max(n, 1), dtype=np.float64)
        return np.clip(np.interp(x, np.arange(a.size), a), -32768, 32767).astype("<i2").tobytes()
    except Exception:
        return b""


class SoundDeviceMic:
    """sounddevice RawInputStream -> int16 mono 16 kHz blocks. Tries 16 kHz mono first (PortAudio /
    MME resample), then the device's own rate and channel count (44.1 / 48 kHz, stereo headsets) with
    our resampling: a device refusing 16 kHz no longer means « micro introuvable »."""

    def __init__(self, device: Any = None) -> None:
        import sounddevice as sd  # noqa: PLC0415

        self.rate, self.channels = SAMPLE_RATE, 1
        tries: list[tuple[int, int]] = [(SAMPLE_RATE, 1)]
        try:
            info = sd.query_devices(device, "input")
            dr, dc = int(float(info.get("default_samplerate") or 48000)), int(info.get("max_input_channels") or 1)
            for t in ((dr, 1), (dr, min(2, max(1, dc))), (48000, 1), (44100, 1), (48000, 2)):
                if t not in tries and t[1] >= 1:
                    tries.append(t)
        except Exception:
            tries += [(48000, 1), (44100, 1), (48000, 2)]
        last: Exception | None = None
        self._s = None
        for rate, ch in tries:
            try:
                st = sd.RawInputStream(samplerate=rate, blocksize=int(rate * BLOCK_S), device=device,
                                       channels=ch, dtype="int16")
                st.start()
                self._s, self.rate, self.channels = st, rate, ch
                break
            except Exception as exc:
                last = exc
        if self._s is None:
            raise last or OSError("no input stream")
        log.info("Assistant vocal : micro ouvert (%d Hz, %d canal%s)", self.rate, self.channels,
                 "" if self.channels == 1 else "aux")

    def read(self) -> bytes | None:
        try:
            data, _overflow = self._s.read(int(self.rate * BLOCK_S))
            data = bytes(data)
            if self.rate != SAMPLE_RATE or self.channels != 1:
                data = resample_to_16k(data, self.rate, self.channels)
            return data
        except Exception:
            log.debug("mic read failed", exc_info=True)
            return None

    def close(self) -> None:
        try:
            self._s.stop()
            self._s.close()
        except Exception:
            pass


def list_mics() -> list[tuple[str, str]]:
    """``[(value, label)]`` of the input devices ("" = Windows default), MME names (one per device)."""
    out: list[tuple[str, str]] = [("", "Micro par défaut de Windows")]
    try:
        import sounddevice as sd  # noqa: PLC0415

        apis = sd.query_hostapis()
        seen: set[str] = set()
        for d in sd.query_devices():
            if int(d.get("max_input_channels", 0) or 0) <= 0:
                continue
            api = str(apis[d["hostapi"]]["name"]) if 0 <= d.get("hostapi", -1) < len(apis) else ""
            name = str(d.get("name") or "").strip()
            if sys.platform == "win32" and api != "MME":
                continue
            if name and name not in seen and "mappeur" not in name.lower() and "mapper" not in name.lower():
                seen.add(name)
                out.append((name, name))
    except Exception:
        log.debug("list_mics failed", exc_info=True)
    return out


def _device_index(name: str) -> Any:
    if not name:
        return None
    try:
        import sounddevice as sd  # noqa: PLC0415

        apis = sd.query_hostapis()
        for i, d in enumerate(sd.query_devices()):
            if int(d.get("max_input_channels", 0) or 0) > 0 and str(d.get("name") or "").strip() == name:
                if sys.platform != "win32" or str(apis[d["hostapi"]]["name"]) == "MME":
                    return i
    except Exception:
        pass
    return None


# ------------------------------------------------------------------ model (downloaded once)
def model_dir() -> Path:
    from treeaicoach import paths  # noqa: PLC0415

    return paths.cache_dir() / "voice" / MODEL_NAME


def model_ready(path: Path | None = None) -> bool:
    p = path or model_dir()
    return (p / "am" / "final.mdl").is_file() and (p / "conf").is_dir()


def download_model(dest: Path | None = None, url: str = MODEL_URL,
                   progress: Callable[[int], None] | None = None,
                   opener: Callable[[str], Any] | None = None) -> bool:
    """Download + unzip the French model into ``dest`` (atomic rename). Never raises."""
    dest = dest or model_dir()
    if model_ready(dest):
        return True
    tmp_zip = dest.parent / (dest.name + ".zip.part")
    tmp_dir = dest.parent / (dest.name + ".tmp")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if opener is None:
            import urllib.request  # noqa: PLC0415

            opener = lambda u: urllib.request.urlopen(u, timeout=30)  # noqa: E731
        with opener(url) as resp, open(tmp_zip, "wb") as fh:
            total = int(getattr(resp, "headers", {}).get("Content-Length", 0) or 0) if hasattr(resp, "headers") else 0
            got = 0
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
                if progress is not None and total:
                    progress(int(100 * got / total))
        shutil.rmtree(tmp_dir, ignore_errors=True)
        with zipfile.ZipFile(tmp_zip) as zf:
            root = tmp_dir.resolve()
            for member in zf.infolist():
                target = (tmp_dir / member.filename).resolve()
                if not str(target).startswith(str(root)):
                    raise ValueError(f"unsafe zip entry {member.filename!r}")
            zf.extractall(tmp_dir)
        inner = tmp_dir / dest.name
        src = inner if inner.is_dir() else tmp_dir
        if not model_ready(src):
            raise ValueError("model archive incomplete")
        shutil.rmtree(dest, ignore_errors=True)
        src.rename(dest)
        return True
    except Exception:
        log.warning("Voice assistant: model download failed", exc_info=True)
        return False
    finally:
        try:
            tmp_zip.unlink(missing_ok=True)
        except Exception:
            pass
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ------------------------------------------------------------------ the assistant
class VoiceAssistant:
    """Background listener. ``configure`` turns it on / off; hooks connect it to the engine:

    * ``should_listen()`` -> in game (the mic is closed otherwise, unless a test runs);
    * ``is_speaking()`` -> the app's own voice is talking (blocks dropped: no self-trigger);
    * ``on_wake()`` -> short chime ("je t'écoute");
    * ``on_question(text)`` -> the engine asks the AI and reads the answer."""

    def __init__(self, *, mic_factory: Callable[[str], Mic] | None = None,
                 recognizer_factory: Callable[[Sequence[str] | None], Recognizer] | None = None,
                 clock: Callable[[], float] = time.monotonic, model_path: Path | None = None) -> None:
        self._mic_factory = mic_factory
        self._rec_factory = recognizer_factory
        self._clock = clock
        self._model_path = model_path
        self._model: Any = None
        self._lock = threading.Lock()
        self._wake_evt = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.enabled = False
        self.mic_name = ""
        self._ptt = False
        self._test_until = -1.0
        self._test_done: Callable[[str, bool], None] | None = None
        self._hotkeys: Any = None
        self._hotkey_key = ""
        self.should_listen: Callable[[], bool] = lambda: False
        self.is_speaking: Callable[[], bool] = lambda: False
        self.on_wake: Callable[[], None] = lambda: None
        self.on_question: Callable[[str], None] = lambda _q: None
        self.status = "Assistant vocal désactivé."
        self.listening = False           # the mic is open (for the UI / tests)
        self.wakes = 0
        self.questions = 0
        self.dropped_self = 0            # blocks dropped while the app was speaking
        self._downloading = False
        self.stage = "off"               # off / loading / ready / listening / wake / question / answer / error
        self.level = 0.0                 # mic level 0..1 (last block)
        self.heard = ""                  # last words the recognizer wrote (the « Écoute » panel)
        self.mic_rate = 0
        self.silent_mic = False
        self._quiet_blocks = 0
        self._speak_since = -1.0
        self._speak_stuck_logged = False

    # -- configuration -------------------------------------------------------------------
    def set_hooks(self, *, should_listen: Callable[[], bool] | None = None,
                  is_speaking: Callable[[], bool] | None = None, on_wake: Callable[[], None] | None = None,
                  on_question: Callable[[str], None] | None = None) -> None:
        if should_listen is not None:
            self.should_listen = should_listen
        if is_speaking is not None:
            self.is_speaking = is_speaking
        if on_wake is not None:
            self.on_wake = on_wake
        if on_question is not None:
            self.on_question = on_question

    def configure(self, cfg: Any) -> None:
        """Apply ``voice_assistant`` / ``voice_assistant_mic`` / ``hotkey_voice_ask``. Never raises."""
        try:
            enabled = bool(getattr(cfg, "voice_assistant", False))
            mic = str(getattr(cfg, "voice_assistant_mic", "") or "")
            with self._lock:
                changed_mic = mic != self.mic_name
                self.enabled, self.mic_name = enabled, mic
            if changed_mic:
                self._wake_evt.set()          # reopen the stream on the new device
            self._sync_hotkey(str(getattr(cfg, "hotkey_voice_ask", "") or "").strip() if enabled else "")
            if enabled:
                self.start()
            else:
                self.status = "Assistant vocal désactivé."
                self._wake_evt.set()
        except Exception:
            log.exception("VoiceAssistant.configure failed")

    def _sync_hotkey(self, key: str) -> None:
        if key == self._hotkey_key:
            return
        old, self._hotkeys, self._hotkey_key = self._hotkeys, None, key
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
        if not key or sys.platform != "win32":
            return
        try:
            from treeaicoach.hotkeys import HotkeyListener  # noqa: PLC0415

            hk = HotkeyListener({key: self.push_to_talk})
            hk.start()
            self._hotkeys = hk
        except Exception:
            log.warning("Assistant vocal : raccourci %s indisponible", key, exc_info=True)

    # -- public actions --------------------------------------------------------------------
    def push_to_talk(self) -> None:
        """Hotkey: listen to a question now (no wake phrase)."""
        with self._lock:
            if not self.enabled:
                return
            self._ptt = True
        self._wake_evt.set()

    def test(self, done: Callable[[str, bool], None] | None = None) -> bool:
        """"Tester : dis « Hey coach »": listen for :data:`TEST_WINDOW_S` even outside a game and even
        with the switch off; ``done(status, ok)`` reports what was heard (the question is NOT sent to
        the AI). Downloads the engine / model first if needed. Never raises."""
        with self._lock:
            self._test_until = self._clock() + TEST_WINDOW_S
            self._test_done = done
        self.status = "Test : dis « Hey coach » puis une question…"
        log.info("Assistant vocal : test lancé (%.0f s)", TEST_WINDOW_S)
        self.start()
        self._wake_evt.set()
        return True

    def snapshot(self) -> dict:
        """The live « Écoute » panel: ``stage`` (+ French ``stage_label``), mic ``level`` 0..1 and its
        ``bar``, ``heard`` (last words the recognizer wrote), ``status``, counters. Any thread, cheap."""
        lvl = float(self.level) if self.listening else 0.0
        return {"stage": self.stage, "stage_label": STAGE_LABELS.get(self.stage, self.stage), "level": lvl,
                "bar": level_bar(lvl), "heard": self.heard, "status": self.status, "listening": self.listening,
                "testing": self._testing(), "wakes": self.wakes, "questions": self.questions,
                "dropped_self": self.dropped_self, "mic_rate": self.mic_rate, "engine_error": ENGINE_ERROR,
                "silent_mic": self.silent_mic}

    def _set_stage(self, stage: str) -> None:
        if stage != self.stage:
            log.info("Assistant vocal : %s -> %s", self.stage, stage)
            self.stage = stage

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="TreeAI-voice-assistant", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake_evt.set()
        self._sync_hotkey("")
        th = self._thread
        if th is not None and th is not threading.current_thread():
            th.join(timeout)
        self._thread = None

    # -- worker ------------------------------------------------------------------------------
    def _testing(self) -> bool:
        return self._clock() < self._test_until

    def _active(self) -> bool:
        if self._testing():
            return True
        if not self.enabled:
            return False
        if self._ptt:
            return True
        try:
            return bool(self.should_listen())
        except Exception:
            return False

    def _speaking(self) -> bool:
        """The app's own voice is busy (mic blocks dropped). A gate stuck on for more than
        :data:`SPEAKING_MAX_S` (a queue that never drains) is ignored: it must not deafen the assistant."""
        try:
            busy = bool(self.is_speaking())
        except Exception:
            busy = False
        now = self._clock()
        if not busy:
            self._speak_since = -1.0
            return False
        if self._speak_since < 0:
            self._speak_since = now
        if now - self._speak_since > SPEAKING_MAX_S:
            if not self._speak_stuck_logged:
                self._speak_stuck_logged = True
                log.warning("Assistant vocal : la voix de l'app semble occupée depuis %.0f s, micro réécouté",
                            now - self._speak_since)
            return False
        return True

    def _make_rec(self, grammar: Sequence[str] | None) -> Recognizer | None:
        if self._rec_factory is not None:
            return self._rec_factory(grammar)
        if self._model is None:
            path = self._model_path or model_dir()
            if not model_ready(path):
                if self._downloading:
                    return None
                self._downloading = True
                self._set_stage("loading")
                self.status = f"Téléchargement du modèle vocal français (~{MODEL_SIZE_MB} Mo)…"
                log.info("Assistant vocal : téléchargement du modèle")
                ok = download_model(path, progress=lambda p: setattr(
                    self, "status", f"Téléchargement du modèle vocal… {p} %"))
                self._downloading = False
                if not ok:
                    self._set_stage("error")
                    self.status = "Modèle vocal non téléchargé (Internet ?) : nouvel essai plus tard."
                    return None
            self._set_stage("loading")
            vosk = import_vosk()
            if vosk is None:
                self._set_stage("error")
                self.status = "Moteur vocal non téléchargé (Internet ?) : nouvel essai plus tard."
                return None
            try:
                vosk.SetLogLevel(-1)
            except Exception:
                pass
            _tune_model_conf(path)
            self._model = vosk.Model(str(path))
            log.info("Assistant vocal : modèle prêt")
        return VoskRecognizer(self._model, grammar)

    def _open_mic(self) -> Mic | None:
        try:
            if self._mic_factory is not None:
                mic = self._mic_factory(self.mic_name)
            else:
                mic = SoundDeviceMic(_device_index(self.mic_name))
            self.mic_rate = int(getattr(mic, "rate", SAMPLE_RATE) or SAMPLE_RATE)
            return mic
        except Exception:
            log.warning("Voice assistant: microphone unavailable", exc_info=True)
            self._set_stage("error")
            self.status = "Micro introuvable : choisis-en un autre dans Alertes et voix."
            return None

    def _observe(self, det: WakeDetector) -> None:
        """Level + last words for the « Écoute » panel; a mic sending only zeros for 10 s is reported."""
        self.level = level_of(det.rms)
        if det.last_text:
            self.heard = det.last_text
        self._quiet_blocks = self._quiet_blocks + 1 if det.rms < SILENT_MIC_RMS else 0
        silent = self._quiet_blocks >= SILENT_MIC_BLOCKS
        if silent != self.silent_mic:
            self.silent_mic = silent
            if silent:
                log.warning("Assistant vocal : le micro n'envoie aucun son depuis 10 s")
                self.status = "Le micro n'envoie aucun son : coupé, ou ce n'est pas le bon ? (Alertes et voix > Micro)"

    def _run(self) -> None:
        mic: Mic | None = None
        wake_rec: Recognizer | None = None
        detector = WakeDetector()
        retry_at = -1.0
        try:
            while not self._stop.is_set():
                if not self._active() or self._clock() < retry_at:
                    if mic is not None:
                        mic.close()
                        mic = None
                        self.listening = False
                    self._finish_test_if_over()
                    if not self.enabled and not self._testing():
                        self._set_stage("off")
                        return
                    if self.stage in ("listening", "wake", "question", "answer"):
                        self._set_stage("ready")
                    self._wake_evt.wait(IDLE_POLL_S)
                    self._wake_evt.clear()
                    continue
                if wake_rec is None:
                    try:
                        wake_rec = self._make_rec(WAKE_GRAMMAR)
                    except Exception as exc:
                        log.warning("Voice assistant: recognizer unavailable", exc_info=True)
                        global ENGINE_ERROR
                        ENGINE_ERROR = mask_error(exc)
                        self._set_stage("error")
                        self.status = "Reconnaissance vocale indisponible sur ce PC."
                        wake_rec = None
                    if wake_rec is None:
                        retry_at = self._clock() + 60.0
                        continue
                    self._set_stage("ready")
                if mic is not None and self._wake_evt.is_set() and not self._ptt:
                    mic.close()                       # device changed
                    mic = None
                self._wake_evt.clear()
                if mic is None:
                    mic = self._open_mic()
                    if mic is None:
                        retry_at = self._clock() + 10.0
                        continue
                    self.listening = True
                    self._set_stage("listening")
                    if not self._testing():
                        self.status = "J'écoute « Hey coach »."
                if self._ptt:
                    with self._lock:
                        self._ptt = False
                    self._question(mic)
                    wake_rec.reset()
                    continue
                block = mic.read()
                if block is None:
                    mic.close()
                    mic = None
                    self.listening = False
                    retry_at = self._clock() + 5.0
                    continue
                if self._speaking():
                    self.dropped_self += 1
                    wake_rec.reset()
                    continue
                self._set_stage("listening")
                woke = detector.feed(wake_rec, block)
                self._observe(detector)
                if woke:
                    self.wakes += 1
                    log.info("Assistant vocal : mot-clé entendu (%s)", detector.last_kind or "?")
                    wake_rec.reset()
                    detector.reset()
                    self._question(mic)
        except Exception:
            log.exception("Voice assistant loop failed")
            self._set_stage("error")
            self.status = "Assistant vocal arrêté après une erreur."
        finally:
            if mic is not None:
                mic.close()
            self.listening = False

    def _question(self, mic: Mic) -> None:
        """Wake heard (or push-to-talk): chime, then free recognition until silence / the time limit."""
        self._set_stage("wake")
        try:
            self.on_wake()
        except Exception:
            pass
        rec = self._make_rec(None)
        if rec is None:
            return
        self._set_stage("question")
        start = self._clock()
        chime_until = start + CHIME_S            # the chime itself does not cut the player's question
        last_change = start
        last_partial = ""
        parts: list[str] = []
        heard_any = False
        while not self._stop.is_set():
            now = self._clock()
            if now - start >= QUESTION_MAX_S:
                break
            if not heard_any and now - start >= QUESTION_START_S:
                break
            if heard_any and now - last_change >= QUESTION_SILENCE_S:
                break
            block = mic.read()
            if block is None:
                break
            if self._clock() >= chime_until and self._speaking():   # our own voice: not the player
                self.dropped_self += 1
                continue
            self.level = level_of(block_rms(block))
            if rec.accept(block):
                txt = rec.result()
                if txt:
                    parts.append(txt)
                    self.heard = " ".join(parts)[-60:]
                    heard_any = True
                    last_change = self._clock()
                    if len(" ".join(parts).split()) >= MIN_QUESTION_WORDS:
                        break
                last_partial = ""
            else:
                p = rec.partial()
                if p and p != last_partial:
                    last_partial = p
                    self.heard = (" ".join(parts + [p]))[-60:]
                    heard_any = True
                    last_change = self._clock()
        if last_partial or not parts:
            tail = rec.final()
            if tail:
                parts.append(tail)
        question = strip_wake(" ".join(parts)).strip()
        if question:
            self.heard = question[-60:]
        self._deliver(question)

    def _deliver(self, question: str) -> None:
        ok = len(question.split()) >= MIN_QUESTION_WORDS
        log.info("Assistant vocal : question de %d mot(s)%s", len(question.split()), "" if ok else " (trop courte)")
        if self._testing():
            done, self._test_done = self._test_done, None
            self._test_until = -1.0
            msg = (f"Entendu : « {question} ». L'assistant marche." if ok else
                   "« Hey coach » entendu, mais pas de question comprise : parle juste après le petit son.")
            self.status = msg
            self._set_stage("ready")
            if done is not None:
                try:
                    done(msg, ok)
                except Exception:
                    pass
            return
        if not ok:
            self.status = "Question non comprise."
            self._set_stage("listening")
            return
        self.questions += 1
        self.status = f"Question : « {question} »"
        self._set_stage("answer")
        try:
            self.on_question(question)
        except Exception:
            log.exception("Voice assistant: on_question failed")

    def _finish_test_if_over(self) -> None:
        if self._test_done is not None and not self._testing():
            done, self._test_done = self._test_done, None
            self.status = ("Test terminé : rien entendu. Le micro n'envoie aucun son : vérifie lequel est choisi."
                           if self.silent_mic else
                           f"Test terminé : « Hey coach » pas reconnu (dernier mot entendu : « {self.heard} »)."
                           if self.heard else "Test terminé : rien entendu. Vérifie le micro choisi.")
            log.info("Assistant vocal : test terminé sans mot-clé")
            try:
                done(self.status, False)
            except Exception:
                pass


_SINGLETON: VoiceAssistant | None = None
_SINGLETON_LOCK = threading.Lock()


def assistant() -> VoiceAssistant:
    """The app's assistant (one per process)."""
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = VoiceAssistant()
        return _SINGLETON


def _tune_model_conf(path: Path) -> None:
    """Wider beam than the small model's default (13 / 7000 active): a bit slower, noticeably better on
    the free-form question (the full 1.5 GB model is not an option to download). Idempotent, never raises."""
    try:
        conf = Path(path) / "conf" / "model.conf"
        txt = conf.read_text(encoding="utf-8")
        new = re.sub(r"--beam=[\d.]+", "--beam=16.0", txt)
        new = re.sub(r"--max-active=\d+", "--max-active=10000", new)
        new = re.sub(r"--lattice-beam=[\d.]+", "--lattice-beam=6.0", new)
        if new != txt:
            conf.write_text(new, encoding="utf-8")
    except Exception:
        log.debug("Voice assistant: model.conf not tuned", exc_info=True)


def import_vosk(dest: Path | None = None) -> Any:
    """The ``vosk`` module: installed / bundled, else its official Windows wheel downloaded once into
    ``cache_dir()/voice/vosk-0.3.45`` (SHA-256 checked) and imported from there. None on failure."""
    try:
        import vosk  # noqa: PLC0415

        return vosk
    except Exception:
        pass
    if sys.platform != "win32":
        return None
    try:
        import hashlib  # noqa: PLC0415
        import io  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415
        import zipfile  # noqa: PLC0415

        from treeaicoach.paths import cache_dir  # noqa: PLC0415

        root = Path(dest) if dest is not None else Path(cache_dir()) / "voice" / "vosk-0.3.45"
        if not (root / "vosk" / "__init__.py").exists():
            with urllib.request.urlopen(VOSK_WHEEL_URL, timeout=60) as r:  # noqa: S310 (fixed https URL)
                data = r.read(80 * 1024 * 1024)
            if hashlib.sha256(data).hexdigest() != VOSK_WHEEL_SHA256:
                log.warning("Voice assistant: vosk wheel checksum mismatch")
                return None
            tmp = root.with_name(root.name + ".part")
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                members = [m for m in zf.namelist() if m.startswith("vosk/")]
                zf.extractall(tmp, members)
            if root.exists():
                import shutil  # noqa: PLC0415

                shutil.rmtree(root, ignore_errors=True)
            tmp.replace(root)
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(root / "vosk"))
        _stub_vosk_deps()
        import vosk  # noqa: PLC0415

        log.info("Assistant vocal : moteur Vosk chargé depuis le cache")
        return vosk
    except Exception as exc:
        global ENGINE_ERROR
        ENGINE_ERROR = mask_error(exc)
        log.warning("Voice assistant: vosk engine unavailable", exc_info=True)
        return None


ENGINE_ERROR = ""      # why the engine could not be loaded (the « Écoute » panel / diagnostics)


def mask_error(exc: BaseException) -> str:
    """``"OSError: cannot load library <chemin>"``: the error without file paths (user name), bounded."""
    txt = re.sub(r"(?:[A-Za-z]:)?[\\/][^\s'\"]+", "<chemin>", f"{type(exc).__name__}: {exc}")
    return txt[:160]


def _stub_vosk_deps() -> None:
    """vosk/__init__.py imports ``srt``, ``requests`` and ``tqdm`` at the top but only uses them for its
    own model downloader / subtitles: when the exe lacks one, an empty stand-in lets the engine load."""
    import importlib.util  # noqa: PLC0415
    import types  # noqa: PLC0415

    for name in ("srt", "requests", "tqdm"):
        try:
            if name in sys.modules or importlib.util.find_spec(name) is not None:
                continue
        except Exception:
            pass
        mod = types.ModuleType(name)
        if name == "tqdm":
            mod.tqdm = object  # type: ignore[attr-defined]
        sys.modules[name] = mod
        log.info("Assistant vocal : module %s absent, remplacé (inutile au moteur)", name)
