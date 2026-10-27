"""Voice assistant « Hey coach » (off by default): the player says the wake phrase, then a question;
the coach answers out loud through the existing AI (manual question path, counted in the per-game cap).

Offline and light:

* speech recognition = Vosk (Kaldi, ``vosk`` wheel, libvosk.dll bundled) with the small French model
  (``vosk-model-small-fr-0.22``, ~41 MB zip) downloaded ONCE into ``paths.cache_dir()/voice`` the first
  time the assistant is switched on (never bundled in the exe);
* microphone = ``sounddevice`` (PortAudio wheel), 16 kHz mono int16, 0.25 s blocks;
* while listening, :class:`WakeEngine` runs a grammar-limited recognizer (the wake phrase variants) AND a
  free one (any French word: « et coach », « Coach, … », « tri coach ») side by side; the free one goes on
  with the question when it was said in the same breath (<= :data:`QUESTION_MAX_S`, ends on silence);
* other device rates (44.1 / 48 kHz, stereo) go through a streaming anti-aliased :class:`Resampler`; a very
  quiet mic (< -40 dBFS speech) gets a slow gain (:class:`Agc`, x8 at most);
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
QUESTION_MAX_S = 12.0           # the question is cut after this
QUESTION_SILENCE_S = 1.8        # ... or ends after this much silence once speech was heard
QUESTION_START_S = 4.0          # nothing said within this after the wake chime: give up
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
_WAKE_RE = re.compile(r"\b(?:hey|hei|he|eh|et|ey|hay|ai|oh|ho|tree|tri|trie|tris)\s+"
                      r"(?:coach|coache|coaches|coachs|coche|coatch|cotch|kotch|couche|cauche|koch|coq)\b")


def normalize(text: Any) -> str:
    """Lower case, no accents, single spaces."""
    s = unicodedata.normalize("NFD", str(text or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", s).split())


#: Last « Hey coach » heard by the PC mic or by Discord for the player himself (monotonic s): both
#: listen to him, the first one answers and the other stays quiet for WAKE_DEDUP_S.
WAKE_DEDUP_S = 4.0
#: The PC-microphone assistant (push-to-talk key + « Hey coach »): only the player's own voice.
PC_MIC_ENABLED = True
_last_wake = {"pc": -1e9, "discord": -1e9}


def claim_wake(source: str, now: float | None = None) -> bool:
    """True when ``source`` ("pc" / "discord") may answer: the other source did not wake in the last
    :data:`WAKE_DEDUP_S` seconds. Records the wake when granted."""
    import time as _t

    t = _t.monotonic() if now is None else now
    other = "discord" if source == "pc" else "pc"
    if t - _last_wake.get(other, -1e9) < WAKE_DEDUP_S:
        return False
    _last_wake[source] = t
    return True


def is_wake(text: Any) -> bool:
    """True when ``text`` (recognizer output) is a variant of « Hey coach »."""
    return bool(_WAKE_RE.search(normalize(text)))


#: wake words: a lead (« hé », « hey », « et »…) then a coach word. STRONG = « coach » itself (what the
#: model writes for the real phrase); WEAK = what it writes for other accents (« hé coche ») but also hears
#: in ordinary talk (« il faut cocher ») -> accepted only when said alone (final result, nothing after).
#: « coq » is never a wake (« Hé, quelqu'un… » is heard « hé coq ») : it stays in the grammar as a decoy.
WAKE_LEADS = frozenset({"hey", "hei", "he", "eh", "et", "ey", "hay", "ai", "e", "oh", "ho", "o",
                        # « Tree coach » / « Hey Tree » (the app's name): what the French model writes for « tree »
                        "tree", "tri", "trie", "tris"})
WAKE_STRONG = frozenset({"coach", "coache", "coaches", "coachs", "coatch", "cotch", "kotch"})
WAKE_WEAK = frozenset({"coche", "couche", "cauche", "koch", "coches", "cocher", "caouche"})
TREE_WORDS = frozenset({"tree", "tri", "trie", "tris"})
_HEY = frozenset({"hey", "hei", "he", "eh", "ey", "hay"})     # « Hey Tree » alone (never « et tri »)
#: any other spelling of /kotʃ/ the free recognizer may write (« kaoche », « coachent »…): weak
_COACH_LIKE = re.compile(r"^(?:c|k|q)(?:o|oa|au|ao|ô)t?(?:ch|sh)(?:e|es|s|ent)?$")


def _coach_class(w: str) -> str:
    """``"strong"`` / ``"weak"`` / ``""`` for one normalized word."""
    if w in WAKE_STRONG:
        return "strong"
    if w in WAKE_WEAK or _COACH_LIKE.match(w):
        return "weak"
    return ""
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
        if final and len(real) == 2 and t[:2] == real and real[0] in WAKE_LEADS \
                and (_coach_class(real[1]) or (real[1] in TREE_WORDS and real[0] in _HEY)) \
                and (not confs or min(float(c) for c in confs[:2]) >= WEAK_MIN_CONF):
            return "weak"                   # « hé coche » / « hey tri » said alone
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
        self.last_final = ""                # the last final result (``[unk]`` removed)
        self.partial_text = ""              # the utterance in progress ("" between utterances)
        self.rms = 0.0

    def reset(self) -> None:
        self._run = 0
        self.partial_text = ""
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
                self.partial_text = ""
                self.last_final = " ".join(str(text).replace("[unk]", " ").split())
                ok = bool(kind) and (kind != "bare" or self._after_quiet)
                self._after_quiet = self._quiet >= QUIET_BLOCKS
                self._note(text, kind if ok else "")
                return ok
            text, _ = self._out(rec, False)
            self.partial_text = " ".join(str(text).replace("[unk]", " ").split())
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


FREE_WAKE = True                # a free (any French word) recognizer runs next to the grammar one
FREE_TAIL_BLOCKS = 4            # the free recognizer is fed speech + 0.75 s after it (its utterance can end)
FLUSH_S = 0.75                  # comfort noise fed when the audio stops (Discord sends nothing in silences)
AGC_ENGAGE_RMS = 330.0          # speech peaks below -40 dBFS: a very quiet mic, brought up…
AGC_TARGET_RMS = 1500.0         # … to about -27 dBFS…
AGC_MAX_GAIN = 8.0              # … by at most x8 (+18 dB). Louder mics are never touched.

_NOISE: bytes | None = None


def comfort_noise(seconds: float) -> bytes:
    """``seconds`` of very low noise (rms ~3), int16 16 kHz: what Vosk needs to see an utterance END
    when the audio itself stops. Deterministic. Never raises."""
    global _NOISE
    try:
        if _NOISE is None:
            import numpy as np  # noqa: PLC0415

            _NOISE = np.random.default_rng(7).normal(0.0, 3.0, SAMPLE_RATE * 2).astype("<i2").tobytes()
        n = max(0, int(SAMPLE_RATE * float(seconds))) * 2
        return (_NOISE * (n // len(_NOISE) + 1))[:n]
    except Exception:
        return bytes(max(0, int(SAMPLE_RATE * float(seconds or 0))) * 2)


class Agc:
    """Slow automatic gain for quiet microphones (headset mics set to 10 % in Windows): the speech peak
    level is tracked and brought up to :data:`AGC_TARGET_RMS`, x1..x:data:`AGC_MAX_GAIN`. Gain falls at
    once on loud speech, rises slowly. Loud enough mics are untouched (gain 1). Never raises."""

    def __init__(self) -> None:
        self.env: float | None = None
        self.gain = 1.0
        self._floor: float | None = None

    def process(self, block: bytes) -> bytes:
        try:
            import numpy as np  # noqa: PLC0415

            a = np.frombuffer(block[: len(block) // 2 * 2], dtype="<i2").astype(np.float32)
            if not a.size:
                return block
            r = float(np.sqrt(np.mean(a * a)))
            self._floor = r if self._floor is None else min(r, self._floor * 1.02 + 1.0)
            if r > max(40.0, 3.0 * self._floor):                     # speech-like block
                self.env = r if self.env is None else max(r, self.env * 0.97)
            if self.env:
                want = 1.0 if self.env >= AGC_ENGAGE_RMS else \
                    max(1.0, min(AGC_MAX_GAIN, AGC_TARGET_RMS / max(self.env, 1.0)))
                self.gain = want if want < self.gain else min(want, self.gain * 1.15)
            if self.gain <= 1.01:
                return block
            return np.clip(a * self.gain, -32768, 32767).astype("<i2").tobytes()
        except Exception:
            return block


class WakeEngine:
    """The always-on « Hey coach » listener of ONE audio stream (the PC mic, or one Discord member):

    * the grammar recognizer (:data:`WAKE_GRAMMAR` + ``[unk]``: forced to choose between the wake variants
      and « something else ») AND a free recognizer (any French word: « et coach », « coach il est où »,
      « hey tri coach ») side by side, each with its :class:`WakeDetector`; either one wakes;
    * a slow :class:`Agc` in front of both (quiet mics), the meter shows the RAW level;
    * :meth:`flush` feeds comfort noise so an utterance whose audio just STOPPED (Discord) still ends;
    * after a wake, :meth:`take_question` hands over what was already said after the wake phrase (« Hey
      coach quel objet » in one breath): the free recognizer itself when mid-utterance, else the words.

    ``make_rec(grammar | None)`` builds recognizers (raises / None when the engine is missing)."""

    def __init__(self, make_rec: Callable[[Any], Any], *, free: bool | None = None, agc: bool = True) -> None:
        self._make_rec = make_rec
        self.free = FREE_WAKE if free is None else bool(free)
        self.grammar_rec: Any = None
        self.free_rec: Any = None
        self.det = WakeDetector()
        self.det_free = WakeDetector()
        self.agc = Agc() if agc else None
        self.rms = 0.0
        self.last_text = ""
        self.last_kind = ""
        self.last_source = ""            # "grammaire" / "libre"
        self._handover: Any = None
        self._carry: list[str] = []
        self._last_logged = ""
        self._log_at = -1e9
        self._free_tail = 0

    def ready(self) -> bool:
        """Recognizers built (lazily). The grammar one is required (its errors propagate); the free one
        is optional."""
        if self.grammar_rec is None:
            self.grammar_rec = self._make_rec(WAKE_GRAMMAR)
            if self.grammar_rec is None:
                return False
        if self.free and self.free_rec is None:
            try:
                self.free_rec = self._make_rec(None)
            except Exception:
                log.debug("free wake recognizer unavailable", exc_info=True)
                self.free = False
        return True

    def feed(self, block: bytes) -> bool:
        """One 16 kHz mono block; True = wake phrase heard. Never raises (False)."""
        try:
            if not self.ready():
                return False
            self.rms = block_rms(block)
            blk = self.agc.process(block) if self.agc is not None else block
            woke = self.det.feed(self.grammar_rec, blk)
            if self.det._quiet == 0:                 # speech: the free recognizer runs, and 0.75 s after
                self._free_tail = FREE_TAIL_BLOCKS
            woke_f = False
            if self.free_rec is not None and self._free_tail > 0 and not woke:
                self._free_tail -= 1                 # (silence costs nothing: ~16 % of a core while fed)
                woke_f = self.det_free.feed(self.free_rec, blk)
            self.det_free.rms = self.det.rms
            text = self.det_free.last_text if self.free_rec is not None and self.det_free.last_text \
                else self.det.last_text
            if text:
                self.last_text = text
            if self.det_free.last_final and self.det_free.last_final != self._last_logged \
                    and time.monotonic() - self._log_at >= 3.0:
                self._last_logged, self._log_at = self.det_free.last_final, time.monotonic()
                log.info("Écoute : entendu « %s »", self._last_logged[:60])
            if not (woke or woke_f):
                return False
            src = self.det_free if woke_f else self.det
            self.last_kind, self.last_source = src.last_kind or "?", "libre" if woke_f else "grammaire"
            self._prepare_handover(woke_f)
            return True
        except Exception:
            log.debug("wake engine: block failed", exc_info=True)
            return False

    def _prepare_handover(self, woke_free: bool) -> None:
        self._handover, self._carry = None, []
        fd = self.det_free
        if self.free_rec is not None:
            if fd.partial_text:                      # mid-utterance: the question goes on in this recognizer
                self._handover, self._carry = self.free_rec, []
                self.free_rec = None                 # a fresh one is built for the next wake
            elif woke_free and fd.last_final:        # « hé coach quel objet » ended in one final result
                if strip_wake(fd.last_final).strip():
                    self._carry = [fd.last_final]
        try:
            self.grammar_rec.reset()
        except Exception:
            pass
        if self.free_rec is not None:
            try:
                self.free_rec.reset()
            except Exception:
                pass
        self.det.reset()
        fd.reset()

    def take_question(self) -> tuple[Any, list[str], str]:
        """``(recognizer to go on with or None, words already heard, the partial in progress)`` for the
        question after a wake. One shot."""
        rec, carry, self._handover, self._carry = self._handover, self._carry, None, []
        partial = ""
        if rec is not None:
            try:
                partial = str(rec.partial() or "")
            except Exception:
                partial = ""
        return rec, carry, partial

    def reset(self) -> None:
        for r in (self.grammar_rec, self.free_rec):
            if r is not None:
                try:
                    r.reset()
                except Exception:
                    pass
        self.det.reset()
        self.det_free.reset()

    def flush(self, seconds: float = FLUSH_S) -> bool:
        """Comfort noise after the audio stopped, so the recognizers end the utterance (a wake said just
        before a silence fires here). True = woke."""
        if self.grammar_rec is None:
            return False
        noise = comfort_noise(seconds)
        n = BLOCK_FRAMES * 2
        for i in range(0, len(noise) - n + 1, n):
            if self.feed(noise[i:i + n]):
                return True
        return False


_WW_LEADS = frozenset({"hey", "hei", "he", "eh", "et", "ey", "hay", "ai", "e", "hi", "oh", "ho", "a", "ok", "okay"})
_WW_TARGETS = ("coach", "coatch", "tree", "tri")
#: what Vosk wrote that MAY be « coach » / « tree » said with another accent: only such utterances are
#: re-checked by Whisper (CPU), ordinary talk is not
_WAKEISH = re.compile(r"\b(?:co|ko|qo|cau|cao|tri|tree|tchi)\w*")
WHISPER_WAKE_PER_MIN = 6


def whisper_wake(text: Any) -> tuple[bool, str]:
    """``(wake, question)`` for a Whisper transcript: it starts with a fuzzy « hey / hé / et coach | tree »
    (« Hé, coach ! », « Et Coatch quel objet », « Hey Tree, … »). ``question`` = what follows."""
    import difflib  # noqa: PLC0415

    words = str(text or "").split()
    norm = [normalize(w) for w in words]
    for lead_n in (1, 0):                            # « hey coach » / leading words dropped by Whisper
        if len(norm) <= lead_n:
            continue
        if lead_n and norm[0] not in _WW_LEADS:
            continue
        w = norm[lead_n]
        if not w:
            continue
        best = max(difflib.SequenceMatcher(None, w, t).ratio() for t in _WW_TARGETS)
        if best >= 0.75 or w in WAKE_STRONG or w in WAKE_WEAK or (lead_n and w in TREE_WORDS):
            if not lead_n and w not in WAKE_STRONG and best < 0.9:
                continue                             # a bare word must really be « coach »
            if not lead_n and len(norm) > 1 and not words[0].endswith((",", "!", "?")) \
                    and norm[1] in ("est", "il", "a", "de", "du", "va", "dit"):
                continue                             # « Coach est nul » / « coach du… »: talk ABOUT the coach
            rest = " ".join(words[lead_n + 1:]).lstrip(" ,.!?;:-")
            return True, rest
    return False, ""


def wakeish(text: Any) -> bool:
    return bool(_WAKEISH.search(normalize(text)))


def question_text(parts: Sequence[str], partial: str = "") -> str:
    """The question so far: what was heard, without the wake phrase."""
    return strip_wake(" ".join([p for p in parts if p] + ([partial] if partial else []))).strip()


def strip_wake(text: Any) -> str:
    """The question without a leading wake phrase (« hé coach quel item » -> « quel item »)."""
    s = str(text or "").strip()
    m = _WAKE_RE.search(normalize(s))
    words = s.split()
    if m is None:
        if words and normalize(words[0]) in WAKE_STRONG:      # « Coach, il est où le jungler ? »
            return " ".join(words[1:])
        return s
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
        else:
            self._r = vosk.KaldiRecognizer(model, SAMPLE_RATE)
        try:
            self._r.SetWords(True)          # word confidences (« Coach » alone must be a sure word)
        except Exception:
            pass
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


class Resampler:
    """Streaming int16 interleaved ``channels`` at ``rate`` -> int16 mono 16 kHz, for a stream cut in
    blocks (0.25 s mic reads, 20 ms Discord packets): channel mean, a 63-tap windowed-sinc low-pass at
    7.2 kHz (no aliasing of the 8-24 kHz band into speech) whose history crosses block borders, then
    decimation (48 kHz: 1 sample in 3) or linear interpolation with the fractional position carried over
    (44.1 kHz). Blocks join without a click. Never raises (b"" on bad input)."""

    TAPS = 63

    def __init__(self, rate: int, channels: int = 1) -> None:
        import numpy as np  # noqa: PLC0415

        self.rate, self.channels = int(rate), max(1, int(channels))
        self.step = self.rate / SAMPLE_RATE
        n = np.arange(self.TAPS) - (self.TAPS - 1) / 2
        fc = 7200.0 / self.rate
        h = 2 * fc * np.sinc(2 * fc * n) * np.blackman(self.TAPS)
        self._h = (h / h.sum()).astype(np.float64)
        self._hist = np.zeros(self.TAPS - 1, np.float64)
        self._pos = 0.0            # position of the next output sample, in filtered input samples
        self._ylast = 0.0

    def process(self, pcm: bytes) -> bytes:
        try:
            import numpy as np  # noqa: PLC0415

            ch = self.channels
            a = np.frombuffer(pcm[: len(pcm) // (2 * ch) * 2 * ch], dtype="<i2").astype(np.float64)
            a = a.reshape(-1, ch).mean(axis=1)
            if self.rate == SAMPLE_RATE:
                return np.clip(np.round(a), -32768, 32767).astype("<i2").tobytes()
            if a.size == 0:
                return b""
            x = np.concatenate([self._hist, a])
            y = np.convolve(x, self._h, mode="valid")          # len == a.size, aligned to a
            self._hist = x[-(self.TAPS - 1):]
            if self.step == int(self.step):                    # 48 kHz, 32 kHz: plain decimation
                k = int(self.step)
                start = int(self._pos)
                out = y[start::k]
                self._pos = (start - y.size) % k
            else:                                              # 44.1 kHz: interpolation, phase carried
                ye = np.concatenate([[self._ylast], y])        # ye[i + 1] == y[i]; ye[0] = last of before
                idx = np.arange(self._pos, y.size - 1, self.step)
                i0 = np.floor(idx).astype(np.int64)
                f = idx - i0
                out = ye[i0 + 1] * (1 - f) + ye[i0 + 2] * f
                self._pos = (idx[-1] + self.step - y.size) if idx.size else self._pos - y.size
                self._ylast = float(y[-1])
            return np.clip(np.round(out), -32768, 32767).astype("<i2").tobytes()
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
        self._rs = Resampler(self.rate, self.channels) if (self.rate, self.channels) != (SAMPLE_RATE, 1) else None
        log.info("Assistant vocal : micro ouvert (%d Hz, %d canal%s)", self.rate, self.channels,
                 "" if self.channels == 1 else "aux")

    def read(self) -> bytes | None:
        try:
            data, _overflow = self._s.read(int(self.rate * BLOCK_S))
            data = bytes(data)
            if self._rs is not None:
                data = self._rs.process(data)
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
    """``[(value, label)]`` for the « Micro » dropdown: "" = the Windows default recording device (its name
    shown), then every input (MME names, one per device: :func:`audio_devices.list_inputs`)."""
    out: list[tuple[str, str]] = [("", "Micro par défaut de Windows")]
    try:
        from treeaicoach import audio_devices as ad  # noqa: PLC0415

        dn = ad.default_input_name()
        if dn:
            out[0] = ("", f"Par défaut de Windows ({dn})")
        out += [(d["name"], d["name"]) for d in ad.list_inputs()]
    except Exception:
        log.debug("list_mics failed", exc_info=True)
    return out


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
                 clock: Callable[[], float] = time.monotonic, model_path: Path | None = None,
                 stt: Any = None, probe: Callable[..., list] | None = None) -> None:
        self._mic_factory = mic_factory
        #: local Whisper (stt_local.WhisperSTT): the real app uses the process-wide one, tests inject a fake;
        #: with injected recognizers and no ``stt`` the assistant stays Vosk-only (deterministic tests)
        self._stt_obj = stt
        self._stt_injected = stt is not None
        self._stt_default = stt is None and recognizer_factory is None
        self._stt_loading = False
        self._probe = probe
        self.mic_label = ""              # the device really opened (« Casque (HyperX) »)
        self.mic_db = -96.0
        self.suggested_mic = ""          # « Test micro »: the input that HAS signal when the chosen one is silent
        self._auto_from = ""             # live auto-probe: the configured mic that sent only zeros…
        self._auto_to = ""               # … and the one that hears the player (the Alerts page saves it)
        self._auto_probed = False
        self.whisper_text = ""
        self._ww_times: list[float] = []
        self._last_utt: tuple[float, bytes] = (-1e9, b"")
        self._zero_blocks = 0
        self._rec_factory = recognizer_factory
        self._clock = clock
        self._model_path = model_path
        self._model: Any = None
        self._lock = threading.Lock()
        self._model_lock = threading.Lock()
        self._checking = False
        self.mic_checking = False        # « Test micro 5 s » running
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
        self.peak_rms = 0.0
        self._lvl_blocks = 0
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
            if self._auto_to and mic == self._auto_from:
                mic = self._auto_to                  # the silent mic was replaced live (not saved yet)
            with self._lock:
                changed_mic = mic != self.mic_name
                self.enabled, self.mic_name = enabled, mic
            if changed_mic:
                self._wake_evt.set()          # reopen the stream on the new device
            self._sync_hotkey(str(getattr(cfg, "hotkey_voice_ask", "") or "").strip() if enabled else "")
            if enabled:
                self.start()
                self.whisper_ready()          # Whisper downloads / loads in the background before the game
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

    def mic_check(self, done: Callable[[str, bool], None] | None = None, seconds: float = 5.0) -> bool:
        """« Test micro 5 s »: records ``seconds`` from the chosen mic (even with the switch off, outside a
        game) with the free recognizer and reports the transcript and the max level through
        ``done(message, ok)``; the « Écoute » row shows the level live. False when one already runs."""
        with self._lock:
            if self._checking:
                return False
            self._checking = True
        threading.Thread(target=self._mic_check_run, args=(done, float(seconds)), name="TreeAI-mic-check",
                         daemon=True).start()
        return True

    def _mic_check_run(self, done: Callable[[str, bool], None] | None, seconds: float) -> None:
        """Records ``seconds``: device name, peak dB, all-zero (Windows privacy) / exclusive-mode diagnosis,
        auto-probe of the other inputs when this one is silent (``suggested_mic``), and the transcript
        (Whisper when loaded, else Vosk)."""
        import math  # noqa: PLC0415

        msg, ok, mic = "Test micro impossible.", False, None
        self.suggested_mic = ""
        try:
            whisper = self.whisper_ready()               # starts the download in the background if needed
            rec = None
            if not whisper:
                try:
                    rec = self._make_rec(None)
                except Exception as exc:
                    global ENGINE_ERROR
                    ENGINE_ERROR = mask_error(exc)
                    rec = None
                if rec is None:
                    msg = "Test micro : reconnaissance vocale indisponible" + (f" ({ENGINE_ERROR})." if ENGINE_ERROR else ".")
                    return
            mic = self._open_mic()
            if mic is None:
                msg = "Test micro : " + self.status
                return
            label = self.mic_label or "micro par défaut"
            self.mic_checking = True
            self.heard = ""
            agc, peak, parts, partial, chunks = Agc(), 0.0, [], "", []
            for _ in range(max(1, int(seconds / BLOCK_S))):
                block = mic.read()
                if block is None:
                    break
                chunks.append(block)
                r = block_rms(block)
                try:
                    import numpy as np  # noqa: PLC0415

                    peak = max(peak, float(np.abs(np.frombuffer(block, "<i2").astype(np.int32)).max()))
                except Exception:
                    peak = max(peak, r * 1.4)
                self.level = level_of(r)
                if rec is None:
                    continue
                b = agc.process(block)
                if rec.accept(b):
                    t = rec.result()
                    if t:
                        parts.append(t)
                    partial = ""
                else:
                    partial = rec.partial() or ""
                self.heard = " ".join(parts + ([partial] if partial else []))[-60:]
            mic.close()
            mic = None
            if rec is not None:
                tail = rec.final()
                if tail:
                    parts.append(tail)
                text, engine = " ".join(parts).strip(), "Vosk"
            else:
                pcm = b"".join(chunks)
                seg = self._segmenter()
                speech = b"".join(seg.feed(pcm) + [seg.flush() or b""]) if seg is not None else pcm
                text, engine = (self._transcribe(speech) if speech else ""), "Whisper"
                self.whisper_text = text
            db = 20 * math.log10(max(peak, 1.0) / 32768.0)
            self.mic_db = db
            zero = peak <= 0.0
            log.info("Test micro : « %s » %d Hz, pic %.0f dB%s, %s : « %s »", label, self.mic_rate, db,
                     " (tout à zéro)" if zero else "", engine, text[:80])
            head = f"Micro « {label} » ({self.mic_rate} Hz), niveau max {db:.0f} dB. "
            if zero or db < -60.0:
                found = self._probe_others()
                if zero:
                    msg = ("Test micro : aucun son reçu, tout est à zéro. Windows bloque probablement le micro : "
                           "Paramètres > Confidentialité > Microphone, autorise l'accès ET les applications de "
                           "bureau. " + head)
                else:
                    msg = ("Test micro : aucun son reçu (micro coupé, volume à 0, ou pas le bon ; vérifie aussi "
                           "Paramètres > Confidentialité > Microphone). " + head)
                if found:
                    self.suggested_mic = found["name"]
                    msg += f"Le micro « {found['name']} » capte ta voix ({found['db']:.0f} dB) : je le choisis."
            elif text:
                ok = True
                self.heard = text[-60:]
                wake = " « Hey coach » reconnu." if (wake_kind(text) in ("strong", "bare") or is_wake(text)
                                                    or whisper_wake(text)[0]) else ""
                msg = f"Test micro : entendu « {text[:120]} » ({engine}). {head}{wake}".strip()
            else:
                msg = f"Test micro : du son ({head.strip()}) mais aucun mot compris."
            if not whisper and self._stt() is not None:
                st = getattr(self._stt(), "status", "")
                msg += f" (Whisper pas encore prêt : {st})" if st else ""
            if not zero and -60.0 <= db < -40:
                msg += " Micro très faible : monte son volume dans Windows (Son > Enregistrement)."
        except Exception:
            log.warning("Assistant vocal : test micro échoué", exc_info=True)
        finally:
            if mic is not None:
                mic.close()
            self.mic_checking = False
            with self._lock:
                self._checking = False
            log.info("Assistant vocal : %s", msg)
            if done is not None:
                try:
                    done(msg, ok)
                except Exception:
                    pass

    def _auto_probe(self) -> None:
        """The chosen mic sends only zeros in a game (Windows privacy / a dead input): every other input is
        measured 1 s; the one that has signal replaces it NOW (the « Micro » setting is saved when the
        Alerts page is shown). Never raises."""
        try:
            found = self._probe_others()
            if not found or found["name"] == self.mic_name:
                return
            log.warning("Assistant vocal : micro « %s » muet, « %s » capte du son (%.0f dB) : je l'utilise",
                        self.mic_label, found["name"], found["db"])
            with self._lock:
                self._auto_from, self._auto_to = self.mic_name, found["name"]
                self.suggested_mic = self.mic_name = found["name"]
            self.status = (f"Le micro choisi n'envoyait aucun son : j'écoute « {found['name']} » à la place "
                           "(gardé dans Alertes et voix).")
            self._wake_evt.set()                     # reopen the stream on it
        except Exception:
            log.debug("auto probe failed", exc_info=True)

    def take_auto_mic(self) -> str:
        """The live replacement mic to save in the settings ("" when none). One shot (UI thread)."""
        with self._lock:
            name, self._auto_from, self._auto_to = self._auto_to, "", ""
        return name

    def _probe_others(self) -> dict | None:
        """The chosen mic is silent: every other input is measured 1 s (the player keeps talking)."""
        try:
            from treeaicoach import audio_devices as ad  # noqa: PLC0415

            if self._probe is not None:
                results = self._probe()
            elif self._mic_factory is not None:
                return None
            else:
                results = ad.probe_all(skip=ad.resolve(self.mic_name)[0])
            best = ad.pick_best([r for r in results if r.get("name") != self.mic_label])
            if best:
                log.info("Test micro : « %s » a du signal (%.0f dB)", best["name"], best["db"])
            return best
        except Exception:
            log.debug("mic probe failed", exc_info=True)
            return None

    def snapshot(self) -> dict:
        """The live « Écoute » panel: ``stage`` (+ French ``stage_label``), mic ``level`` 0..1 and its
        ``bar``, ``heard`` (last words the recognizer wrote), ``status``, counters. Any thread, cheap."""
        lvl = float(self.level) if self.listening or self.mic_checking else 0.0
        return {"stage": self.stage, "stage_label": STAGE_LABELS.get(self.stage, self.stage), "level": lvl,
                "bar": level_bar(lvl), "heard": self.heard, "status": self.status,
                "listening": self.listening or self.mic_checking, "mic_checking": self.mic_checking,
                "testing": self._testing(), "wakes": self.wakes, "questions": self.questions,
                "dropped_self": self.dropped_self, "mic_rate": self.mic_rate, "engine_error": ENGINE_ERROR,
                "silent_mic": self.silent_mic, "mic_label": self.mic_label, "suggested_mic": self.suggested_mic,
                "auto_mic": self._auto_to, "mic_db": round(self.mic_db, 1),
                "whisper": bool(self._stt_obj is not None and self._stt_obj.ready()),
                "whisper_status": str(getattr(self._stt_obj, "status", "") or ""), "whisper_text": self.whisper_text}

    def _set_stage(self, stage: str) -> None:
        if stage != self.stage:
            log.info("Assistant vocal : %s -> %s", self.stage, stage)
            self.stage = stage

    def start(self) -> None:
        if not PC_MIC_ENABLED:                 # Discord only for now: the PC microphone is never opened
            log.info("Assistant vocal : micro du PC désactivé (écoute par Discord uniquement)")
            return
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
        with self._model_lock:                  # the wake loop, the mic test and Discord share one model
            return self._make_rec_locked(grammar)

    def _make_rec_locked(self, grammar: Sequence[str] | None) -> Recognizer | None:
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
            self._model = vosk.Model(model_path_for_engine(path))
            log.info("Assistant vocal : modèle prêt")
        return VoskRecognizer(self._model, grammar)

    def _open_mic(self) -> Mic | None:
        try:
            if self._mic_factory is not None:
                mic = self._mic_factory(self.mic_name)
                self.mic_label = self.mic_name or "micro par défaut"
            else:
                from treeaicoach import audio_devices as ad  # noqa: PLC0415

                idx, self.mic_label = ad.resolve(self.mic_name)
                mic = SoundDeviceMic(idx)
            self.mic_rate = int(getattr(mic, "rate", SAMPLE_RATE) or SAMPLE_RATE)
            log.info("Assistant vocal : micro « %s » ouvert (%d Hz)", self.mic_label, self.mic_rate)
            return mic
        except Exception as exc:
            log.warning("Voice assistant: microphone unavailable", exc_info=True)
            self._set_stage("error")
            try:
                from treeaicoach import audio_devices as ad  # noqa: PLC0415

                why = ad.explain({"error": f"{type(exc).__name__}: {exc}"})
            except Exception:
                why = ""
            self.status = why if why and "exclusif" in why else \
                "Micro introuvable : choisis-en un autre dans Alertes et voix."
            return None

    def _observe(self, eng: Any) -> None:
        """Level + last words for the « Écoute » panel; a mic sending only zeros for 10 s is reported;
        the level is logged every 30 s while listening (« niveau max -38 dB ») so a log tells a quiet mic."""
        self.level = level_of(eng.rms)
        if eng.last_text:
            self.heard = eng.last_text
        self.peak_rms = max(self.peak_rms, float(eng.rms))
        self._lvl_blocks += 1
        if self._lvl_blocks >= int(30 / BLOCK_S):
            import math  # noqa: PLC0415

            db = 20 * math.log10(max(self.peak_rms, 1.0) / 32768.0)
            log.info("Assistant vocal : niveau max %.0f dB sur 30 s%s", db,
                     " (micro très faible : monte son volume dans Windows)" if db < -40 else "")
            self._lvl_blocks, self.peak_rms = 0, 0.0
        self._quiet_blocks = self._quiet_blocks + 1 if eng.rms < SILENT_MIC_RMS else 0
        self._zero_blocks = self._zero_blocks + 1 if eng.rms <= 0.0 else 0
        silent = self._quiet_blocks >= SILENT_MIC_BLOCKS
        if silent != self.silent_mic:
            self.silent_mic = silent
            if silent:
                zero = self._zero_blocks >= SILENT_MIC_BLOCKS
                log.warning("Assistant vocal : le micro « %s » n'envoie aucun son depuis 10 s%s", self.mic_label,
                            " (tout à zéro : blocage Windows probable)" if zero else "")
                self.status = ("Le micro n'envoie aucun son (tout est à zéro) : Windows le bloque sûrement. "
                               "Paramètres > Confidentialité > Microphone : autorise l'accès ET les applications "
                               "de bureau. Puis « Test micro » dans Alertes et voix." if zero else
                               "Le micro n'envoie aucun son : coupé, pas le bon, ou bloqué par Windows "
                               "(Paramètres > Confidentialité > Microphone : autoriser les applications de bureau). "
                               "« Test micro » (Alertes et voix) cherche celui qui marche.")
                if zero and not self._auto_probed and (self._probe is not None or self._mic_factory is None):
                    self._auto_probed = True             # once per session: find the input that has signal
                    threading.Thread(target=self._auto_probe, name="TreeAI-mic-probe", daemon=True).start()

    def _run(self) -> None:
        mic: Mic | None = None
        wake_rec: WakeEngine | None = None
        seg: Any = None
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
                        eng = WakeEngine(self._make_rec)
                        wake_rec = eng if eng.ready() else None
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
                    seg = self._segmenter()
                    if not self._testing():
                        self.status = "J'écoute « Hey coach »."
                if self._ptt:
                    with self._lock:
                        self._ptt = False
                    self._question(mic)
                    wake_rec.reset()
                    if seg is not None:
                        seg.reset()
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
                woke = wake_rec.feed(block)
                self._observe(wake_rec)
                utts = seg.feed(block) if seg is not None else []
                if utts:
                    self._last_utt = (self._clock(), utts[-1])
                if woke:
                    self.wakes += 1
                    log.info("Assistant vocal : mot-clé entendu (%s, %s) dans « %s »", wake_rec.last_kind,
                             wake_rec.last_source, wake_rec.last_text)
                    prefix = seg.open_audio() if seg is not None else b""
                    if not prefix and self._clock() - self._last_utt[0] <= 2.0:
                        prefix = self._last_utt[1]          # VAD closed the « hey coach » just before Vosk
                    self._question(mic, wake_rec.take_question(), prefix=prefix)
                    wake_rec.reset()
                    if seg is not None:
                        seg.reset()
                    self._last_utt = (-1e9, b"")
                    continue
                for u in utts:
                    if self._whisper_wake_check(mic, u, wake_rec.last_text):
                        wake_rec.reset()
                        if seg is not None:
                            seg.reset()
                        self._last_utt = (-1e9, b"")
                        break
        except Exception:
            log.exception("Voice assistant loop failed")
            self._set_stage("error")
            self.status = "Assistant vocal arrêté après une erreur."
        finally:
            if mic is not None:
                mic.close()
            self.listening = False

    # -- local Whisper (zero request) -------------------------------------------------------------
    def _stt(self) -> Any:
        if self._stt_obj is None and self._stt_default:
            try:
                from treeaicoach import stt_local  # noqa: PLC0415

                self._stt_obj = stt_local.transcriber()
            except Exception:
                self._stt_default = False
        return self._stt_obj

    def _transcribe(self, pcm: bytes) -> str:
        """Whisper text of one utterance. The app's own Whisper goes through THE Whisper thread (one
        transcription at a time with Discord's speakers); the champions of the game bias the prompt."""
        w = self._stt()
        if w is None or not pcm:
            return ""
        if self._stt_obj is not None and self._stt_injected:
            return str(w.transcribe(pcm) or "")
        try:
            from treeaicoach import stt_local  # noqa: PLC0415

            refresh_game_names(w)
            return stt_local.worker().transcribe(pcm, key="pc", timeout=20.0)
        except Exception:
            log.debug("whisper worker failed", exc_info=True)
            return ""

    def whisper_ready(self, load: bool = True) -> bool:
        """Whisper loaded; else (``load``) its download / loading starts in the background (once)."""
        w = self._stt()
        if w is None:
            return False
        try:
            if w.ready():
                return True
            if load and not self._stt_loading and w.available_soon():
                self._stt_loading = True

                def _go() -> None:
                    try:
                        w.load()
                    finally:
                        self._stt_loading = False
                threading.Thread(target=_go, name="TreeAI-whisper-load", daemon=True).start()
        except Exception:
            log.debug("whisper_ready failed", exc_info=True)
        return False

    def _segmenter(self) -> Any:
        try:
            from treeaicoach.vad import Segmenter  # noqa: PLC0415

            return Segmenter()
        except Exception:
            log.debug("VAD unavailable", exc_info=True)
            return None

    def _whisper_wake_check(self, mic: Mic, utt: bytes, vosk_text: str) -> bool:
        """A VAD utterance Vosk did NOT wake on but whose words look like « coach » / « tree » (another
        accent): Whisper reads it (<= :data:`WHISPER_WAKE_PER_MIN` a minute, CPU). True = woke + handled."""
        if not wakeish(vosk_text) or len(utt) > 8 * SAMPLE_RATE * 2 or not self.whisper_ready():
            return False
        now = self._clock()
        self._ww_times = [t for t in self._ww_times if now - t < 60.0]
        if len(self._ww_times) >= WHISPER_WAKE_PER_MIN:
            return False
        self._ww_times.append(now)
        text = self._transcribe(utt)
        ok, rest = whisper_wake(text)
        if not ok:
            return False
        self.wakes += 1
        self.whisper_text = text
        log.info("Assistant vocal : mot-clé entendu par Whisper dans « %s »", text[:80])
        if len(rest.split()) >= MIN_QUESTION_WORDS:
            if not claim_wake("pc"):          # Discord already answered him
                return True
            self._set_stage("wake")
            try:
                self.on_wake()
            except Exception:
                pass
            self.heard = rest[-60:]
            self._deliver(rest)
        else:
            self._question(mic)
        return True

    def _question_whisper(self, mic: Mic, carry: str, prefix: bytes) -> None:
        """After the wake (chime already played): the next VAD utterance (or what was said in the same
        breath, ``prefix``) is read by Whisper. ``carry`` = Vosk's words, the fallback."""
        seg = self._segmenter()
        self._set_stage("question")
        start = self._clock()
        chime_until = start + CHIME_S
        wait = 1.0 if len(carry.split()) >= MIN_QUESTION_WORDS else QUESTION_START_S
        got, started = b"", False
        while not self._stop.is_set() and seg is not None:
            now = self._clock()
            if now - start >= QUESTION_MAX_S + 4.0:
                got = seg.flush() or b""
                break
            if not started and now - start >= wait:
                break
            block = mic.read()
            if block is None:
                break
            if self._clock() >= chime_until and self._speaking():
                self.dropped_self += 1
                continue
            self.level = level_of(block_rms(block))
            us = seg.feed(block)
            started = started or seg.in_speech or bool(us)
            if us:
                got = us[0]
                break
        pcm = prefix + got
        text = self._transcribe(pcm) if pcm else ""
        self.whisper_text = text
        ok, rest = whisper_wake(text)
        q = (rest if ok else strip_wake(text)).strip()
        if len(q.split()) < MIN_QUESTION_WORDS and len(carry.split()) > len(q.split()):
            q = carry
        if q:
            self.heard = q[-60:]
        self._deliver(q)

    def _question(self, mic: Mic, handover: tuple[Any, list[str], str] | None = None,
                  prefix: bytes = b"") -> None:
        if self.whisper_ready():
            if not claim_wake("pc"):          # Discord already answered him
                return
            self._set_stage("wake")
            try:
                self.on_wake()
            except Exception:
                pass
            rec, parts, partial = handover if handover is not None else (None, [], "")
            self._question_whisper(mic, question_text(parts, partial), prefix)
            return
        self._question_vosk(mic, handover)

    def _question_vosk(self, mic: Mic, handover: tuple[Any, list[str], str] | None = None) -> None:
        """Wake heard (or push-to-talk): chime, then free recognition until silence / the time limit.
        ``handover`` = what the wake engine already heard after the wake phrase (« hey coach quel objet »
        said in one breath): its recognizer goes on, its words count."""
        if not claim_wake("pc"):          # Discord already answered him
            return
        self._set_stage("wake")
        try:
            self.on_wake()
        except Exception:
            pass
        rec, parts, last_partial = handover if handover is not None else (None, [], "")
        parts = list(parts)
        if rec is None:
            rec = self._make_rec(None)
        if rec is None:
            return
        self._set_stage("question")
        start = self._clock()
        chime_until = start + CHIME_S            # the chime itself does not cut the player's question
        last_change = start
        heard_any = bool(question_text(parts, last_partial))
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
                    if question_text(parts):
                        heard_any = True
                        last_change = self._clock()
                    if len(question_text(parts).split()) >= MIN_QUESTION_WORDS:
                        break
                last_partial = ""
            else:
                p = rec.partial()
                if p and p != last_partial:
                    last_partial = p
                    self.heard = (" ".join(parts + [p]))[-60:]
                    if question_text(parts, p):
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


_NAMES_AT = [-1e9]
NAMES_TTL_S = 90.0


def game_champions(fetch: Callable[[], Any] | None = None) -> list[str]:
    """The champions of the current game (mine, my team, then the enemies) from the official Live Client
    API on this PC (127.0.0.1:2999, read-only), [] outside a game. Never raises."""
    try:
        if fetch is None:
            from treeaicoach.live_client import LiveClient  # noqa: PLC0415

            fetch = LiveClient(timeout=0.5).fetch
        game = fetch()
        if game is None:
            return []
        me = getattr(game, "me", None)
        players = ([me] if me is not None else []) + list(getattr(game, "allies", None) or []) + \
            list(getattr(game, "enemies", None) or [])
        return [str(getattr(p, "champion_name", "") or getattr(p, "champion_alias", "")) for p in players
                if p is not None and (getattr(p, "champion_name", "") or getattr(p, "champion_alias", ""))]
    except Exception:
        log.debug("game champions unavailable", exc_info=True)
        return []


def refresh_game_names(stt: Any, fetch: Callable[[], Any] | None = None, now: float | None = None) -> None:
    """Whisper's prompt gets the game's champions (« Malphite », « Kha'Zix »…), re-read every
    :data:`NAMES_TTL_S` at most (on a question only). Never raises."""
    try:
        t = time.monotonic() if now is None else now
        if t - _NAMES_AT[0] < NAMES_TTL_S or not hasattr(stt, "set_names"):
            return
        _NAMES_AT[0] = t
        names = game_champions(fetch)
        if names:
            stt.set_names(names)
    except Exception:
        log.debug("refresh_game_names failed", exc_info=True)


_SINGLETON: VoiceAssistant | None = None
_SINGLETON_LOCK = threading.Lock()


def assistant() -> VoiceAssistant:
    """The app's assistant (one per process)."""
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = VoiceAssistant()
        return _SINGLETON


def model_path_for_engine(path: Path) -> str:
    """The model path as libvosk can open it. On Windows libvosk opens files with ANSI (narrow) paths:
    a cache under a user name with accents (« C:\\Users\\Clément\\… ») cannot be opened -> the 8.3 short
    path, else a copy under ``%ProgramData%\\TreeAICoach\\voice``. Never raises."""
    p = str(path)
    if sys.platform != "win32" or p.isascii():
        return p
    try:
        import ctypes  # noqa: PLC0415

        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(p, buf, 1024) and buf.value.isascii():  # type: ignore[attr-defined]
            log.info("Assistant vocal : chemin du modèle raccourci (accents)")
            return buf.value
    except Exception:
        pass
    try:
        dest = Path(os.environ.get("ProgramData") or "C:\\ProgramData") / "TreeAICoach" / "voice" / Path(p).name
        if str(dest).isascii():
            if not model_ready(dest):
                shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(p, dest)
            log.info("Assistant vocal : modèle copié vers un chemin sans accents")
            return str(dest)
    except Exception:
        log.warning("Assistant vocal : copie du modèle impossible", exc_info=True)
    return p


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
