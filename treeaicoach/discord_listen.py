"""« Hey coach » heard in the Discord voice channel (off by default: ``discord_listen``).

discord.py 2.7 has no voice *receive* API, but everything needed is there without a new
dependency: the voice UDP socket can be read (``VoiceConnectionState.add_socket_listener``), the voice
websocket messages can be watched (the ``hook`` of the connection state: SPEAKING gives the
SSRC -> user id map), PyNaCl decrypts the transport (``aead_xchacha20_poly1305_rtpsize``, the mode
discord.py picks), davey's ``DaveSession.decrypt(user_id, MediaType.audio, frame)`` removes the DAVE
end-to-end layer (the bot is a member of the channel's MLS group, so it holds every sender's key) and
``discord.opus.Decoder`` (libopus already shipped for speaking) gives 48 kHz stereo PCM.
(discord-ext-voice-recv, the usual add-on, was last released in June 2025 and knows nothing of DAVE,
mandatory since March 2026: it would hand DAVE ciphertext to the Opus decoder.)

Per speaker: 48 kHz stereo -> 16 kHz mono (per-SSRC streaming low-pass resampler), then the SAME logic as
the PC assistant (voice_assistant.WakeEngine: grammar + free recognizers, then the question, ≤ 6 s, ends on
1.2 s of silence). Packets STOP while someone is silent (Discord's voice activity): the recognizers would
never see the end of « Hey coach », so a ticker feeds them 0.75 s of comfort noise 0.3 s after the last
packet (measured on 48 edge-tts wake clips through Opus: 31/48 woke before, see tests/test_wake_realworld).

Never on the bot's own audio (own SSRC / user id dropped; blocks dropped while the bot plays, so a
friend's speakers can't wake it); rate limits per speaker and overall. Never raises."""
from __future__ import annotations

import collections
import logging
import queue
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

from treeaicoach import voice_assistant as va

log = logging.getLogger(__name__)

RTP_HEADER = 12
OPUS_SILENCE = b"\xf8\xff\xfe"
DAVE_MAGIC = b"\xfa\xfa"                 # the last 2 bytes of a DAVE-encrypted frame
BLOCK_BYTES = va.BLOCK_FRAMES * 2        # 0.25 s of int16 mono 16 kHz
SPEAKER_COOLDOWN_S = 20.0                # one question per person every 20 s
QUESTIONS_WINDOW_S = 600.0
QUESTIONS_MAX = 10                       # and at most 10 questions in 10 minutes, everyone together
BUSY_MAX_S = 25.0                        # a question being answered blocks the next one this long at most
WAKE_RESET_SILENCE_S = 1.5               # a speaker silent this long: his wake recognizer starts afresh
PREROLL_S = 0.25                         # comfort noise in front of a talk spurt
FLUSH_AFTER_S = 0.3                      # packets stopped this long: comfort noise ends the utterance (see tick)
BOT_SPEAKING_MAX_S = 30.0                # the bot « playing » longer than this: the gate is stuck, ignored
QUEUE_MAX = 400                          # 16 kHz blocks waiting for the recognizers (~8 s of 1 speaker)
MAX_SPEAKERS = 8
RING_BLOCKS = 24                         # 6 s of a member's audio kept while waiting for the wake
WAKE_AUDIO_BLOCKS = 16                   # the last 4 s go to Whisper with the question
RING_QUESTION_MIN = WAKE_AUDIO_BLOCKS + 2


# ------------------------------------------------------------------------------ packets
def rtp_payload(packet: bytes, secret_key: Any, mode: str = "aead_xchacha20_poly1305_rtpsize"
                ) -> tuple[int, int, bytes] | None:
    """``(ssrc, sequence, opus frame)`` of one received voice packet (transport layer removed), or
    None (RTCP, IP discovery, another mode, bad key / corrupt packet). Never raises."""
    try:
        if len(packet) < RTP_HEADER + 4 + 16 or (packet[0] >> 6) != 2:
            return None
        if 200 <= packet[1] <= 204:                       # RTCP sender / receiver reports
            return None
        if mode != "aead_xchacha20_poly1305_rtpsize" or not secret_key:
            return None
        cc = packet[0] & 0x0F
        has_ext = bool(packet[0] & 0x10)
        hlen = RTP_HEADER + 4 * cc + (4 if has_ext else 0)
        if len(packet) < hlen + 4 + 16:
            return None
        import nacl.secret  # noqa: PLC0415

        nonce = bytes(packet[-4:]) + b"\x00" * 20
        plain = nacl.secret.Aead(bytes(secret_key)).decrypt(bytes(packet[hlen:-4]), bytes(packet[:hlen]), nonce)
        if has_ext:
            ext_words = struct.unpack_from(">H", packet, hlen - 2)[0]
            plain = plain[4 * ext_words:]
        ssrc = struct.unpack_from(">I", packet, 8)[0]
        seq = struct.unpack_from(">H", packet, 2)[0]
        return ssrc, seq, bytes(plain)
    except Exception:
        return None


def dave_decrypt(session: Any, user_id: int, frame: bytes) -> bytes | None:
    """The Opus frame without the DAVE layer: as is when there is no DAVE session (or a frame sent in
    passthrough during a transition), None when it cannot be decrypted. Never raises."""
    if session is None or not frame or frame == OPUS_SILENCE:
        return frame
    try:
        if not getattr(session, "ready", False):
            return None if frame.endswith(DAVE_MAGIC) else frame
        import davey  # noqa: PLC0415

        return bytes(session.decrypt(int(user_id), davey.MediaType.audio, frame))
    except Exception:
        return None if frame.endswith(DAVE_MAGIC) else frame


def to_16k_mono(pcm48: bytes) -> bytes:
    """48 kHz stereo int16 -> 16 kHz mono int16 (channel mean, then 3-sample mean + decimation:
    enough low-pass for speech recognition). Never raises (b"" on bad input)."""
    try:
        import numpy as np  # noqa: PLC0415

        a = np.frombuffer(pcm48, dtype="<i2")
        a = a[: len(a) // 6 * 6].astype(np.int32).reshape(-1, 3, 2)
        return (a.sum(axis=(1, 2)) // 6).astype("<i2").tobytes()
    except Exception:
        return b""


# ------------------------------------------------------------------------------ one speaker
class Speaker:
    """« Hey coach » then the question, for ONE member of the channel (fed 16 kHz mono blocks)."""

    def __init__(self, uid: str, make_rec: Callable[[Any], Any], clock: Callable[[], float],
                 stt: Callable[[], Any] | None = None) -> None:
        self.uid = uid
        self._stt = stt or (lambda: None)     # local Whisper when loaded (re-reads the question audio)
        self.ring: collections.deque[bytes] = collections.deque(maxlen=RING_BLOCKS)   # real audio, wake phase
        self.q_audio: list[bytes] = []
        self.seg: Any = None                  # Silero VAD of this member (question end)
        self.q_spoke = False
        self.whisper_text = ""
        self._make_rec = make_rec
        self._clock = clock
        self._buf = bytearray()
        self.engine = va.WakeEngine(make_rec)
        self.flushed = True                 # comfort noise already fed since the last packet
        self.q_rec: Any = None              # set while a question is being recorded
        self.q_start = 0.0
        self.q_change = 0.0
        self.q_heard = False
        self.q_parts: list[str] = []
        self.q_partial = ""
        self.last_audio = -1e9
        self.level = 0.0                    # the « Écoute » row: level 0..1, last words, stage
        self.heard = ""
        self.stage = "écoute"
        self.blocks = 0

    @property
    def wake_rec(self) -> Any:
        return self.engine.grammar_rec

    @property
    def detector(self) -> va.WakeDetector:
        return self.engine.det

    def feed(self, pcm16: bytes) -> list[tuple[str, str]]:
        """Events: ``("wake", "")`` / ``("question", text)``."""
        self.last_audio = self._clock()
        if self.flushed and self.q_rec is None and not self._buf:
            # a talk spurt starts (Discord sent nothing before): a short silence in front, as a mic would
            # give, so the recognizers start from silence and « Hé… » is not glued to nothing
            self._buf += va.comfort_noise(PREROLL_S)
        self.flushed = False
        self._buf += pcm16
        out: list[tuple[str, str]] = []
        while len(self._buf) >= BLOCK_BYTES:
            block, self._buf = bytes(self._buf[:BLOCK_BYTES]), self._buf[BLOCK_BYTES:]
            out += self._block(block)
        return out

    def _block(self, block: bytes) -> list[tuple[str, str]]:
        self.blocks += 1
        if self.q_rec is not None:
            self.q_audio.append(block)
            if self.seg is not None:
                closed = self.seg.feed(block)
                self.q_spoke = self.q_spoke or self.seg.in_speech or bool(closed)
                if closed and len(self.q_audio) > RING_QUESTION_MIN:
                    return self._end()           # the VAD heard the question end
            rec = self.q_rec
            self.level = va.level_of(va.block_rms(block))
            if rec.accept(block):
                txt = rec.result()
                if txt:
                    self.q_parts.append(txt)
                    self.heard = " ".join(self.q_parts)[-60:]
                    if va.question_text(self.q_parts):
                        self.q_heard, self.q_change = True, self._clock()
                    if self.seg is None and len(va.question_text(self.q_parts).split()) >= va.MIN_QUESTION_WORDS:
                        return self._end()           # (with Whisper, the VAD / silence ends it: whole question)
                self.q_partial = ""
            else:
                p = rec.partial()
                if p and p != self.q_partial:
                    self.q_partial = p
                    self.heard = " ".join(self.q_parts + [p])[-60:]
                    if va.question_text(self.q_parts, p):
                        self.q_heard, self.q_change = True, self._clock()
            return self.tick()
        try:
            ok = self.engine.ready()
        except Exception:
            log.debug("Discord listen: recognizer unavailable", exc_info=True)
            ok = False
        if not ok:
            self.stage = "modèle indisponible"
            return []
        self.ring.append(block)
        return self._wake_block(block)

    def _wake_block(self, block: bytes, noise: bool = False) -> list[tuple[str, str]]:
        eng = self.engine
        woke = eng.feed(block)
        if not noise:
            self.level = va.level_of(eng.rms)
        if eng.last_text:
            self.heard = eng.last_text
        if woke:
            self.stage = "mot-clé entendu"
            log.info("Discord écoute : mot-clé entendu (%s, %s) dans « %s »", eng.last_kind, eng.last_source,
                     eng.last_text)
            return [("wake", "")]
        if self.stage != "écoute" and self.q_rec is None:
            self.stage = "écoute"
        return []

    def view(self, now: float) -> dict:
        """The member's « Écoute » line: level (0 once silent for 1 s), last words, stage."""
        live = now - self.last_audio < 1.0
        lvl = self.level if live else 0.0
        return {"level": lvl, "bar": va.level_bar(lvl), "heard": self.heard,
                "stage": "question" if self.q_rec is not None else self.stage,
                "age_s": round(max(0.0, now - self.last_audio), 1) if self.last_audio > 0 else None}

    def start_question(self) -> bool:
        rec, parts, partial = self.engine.take_question()     # « hey coach quel objet » in one breath
        if rec is None:
            rec = self._make_rec(None)
        if rec is None:
            return False
        self.q_rec, self.q_start = rec, self._clock()
        self.q_parts, self.q_partial = list(parts), partial
        self.q_change, self.q_heard = self.q_start, bool(va.question_text(self.q_parts, partial))
        self.stage = "question"
        self.q_audio = list(self.ring)[-WAKE_AUDIO_BLOCKS:]     # « hey coach … » itself (Whisper strips it)
        self.ring.clear()
        self.q_spoke = False
        if self._stt() is not None:
            try:
                from treeaicoach.vad import Segmenter  # noqa: PLC0415

                self.seg = Segmenter()
            except Exception:
                self.seg = None
        return True

    def tick(self) -> list[tuple[str, str]]:
        now = self._clock()
        if self.q_rec is None:
            if self.engine.grammar_rec is None or self.last_audio <= 0:
                return []
            gap = now - self.last_audio
            if gap >= FLUSH_AFTER_S and not self.flushed:
                # Discord sends NO packet while someone is silent: without this the recognizers never see
                # the end of « Hey coach » (no final result) and the next words glue to it
                self.flushed = True
                self._buf.clear()
                n = va.BLOCK_FRAMES * 2
                noise = va.comfort_noise(va.FLUSH_S)
                for i in range(0, len(noise) - n + 1, n):
                    ev = self._wake_block(noise[i:i + n], noise=True)
                    if ev:
                        return ev
            if gap >= WAKE_RESET_SILENCE_S:
                self.engine.reset()
                self.last_audio = 0.0
            return []
        if self.seg is not None and self.q_spoke and now - self.last_audio >= FLUSH_AFTER_S + 0.3:
            return self._end()                   # Discord stopped sending: the question is over
        if (now - self.q_start >= va.QUESTION_MAX_S
                or (not self.q_heard and now - self.q_start >= va.QUESTION_START_S)
                or (self.q_heard and now - self.q_change >= va.QUESTION_SILENCE_S)):
            return self._end()
        return []

    def _end(self) -> list[tuple[str, str]]:
        rec, self.q_rec = self.q_rec, None
        if rec is not None and (self.q_partial or not self.q_parts):
            try:
                tail = rec.final()
            except Exception:
                tail = ""
            if tail:
                self.q_parts.append(tail)
        q = va.strip_wake(" ".join(self.q_parts)).strip()
        stt = self._stt()
        audio, self.q_audio, self.seg = b"".join(self.q_audio), [], None
        if stt is not None and audio:
            text = stt.transcribe(audio)
            self.whisper_text = text
            woke, rest = va.whisper_wake(text)
            wq = (rest if woke else va.strip_wake(text)).strip()
            if len(wq.split()) >= va.MIN_QUESTION_WORDS:
                q = wq
        if q:
            self.heard = q[-60:]
        self.stage = "écoute"
        self.engine.reset()
        return [("question", q)]


# ------------------------------------------------------------------------------ the channel
class VoiceListener:
    """Every speaker of the channel. ``push(user_id, pcm16)`` from the socket thread (cheap: a queue);
    a worker thread runs the recognizers and the timers.

    Hooks: ``should_listen()`` (switch on + in game), ``bot_speaking()`` (the bot plays: blocks
    dropped), ``on_wake(uid)`` (chime in the channel), ``on_question(uid, text)`` (ask the AI)."""

    def __init__(self, *, make_rec: Callable[[Any], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic, threaded: bool = True, stt: Any = None) -> None:
        self._make_rec_fn = make_rec
        self._stt = stt
        self._clock = clock
        self._q: queue.Queue = queue.Queue(QUEUE_MAX)
        self._speakers: dict[str, Speaker] = {}
        self._asked: collections.deque[float] = collections.deque()
        self._last_q: dict[str, float] = {}
        self._busy_until = -1e9
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threaded = threaded
        self._thread: threading.Thread | None = None
        self.should_listen: Callable[[], bool] = lambda: False
        self.bot_speaking: Callable[[], bool] = lambda: False
        self.on_wake: Callable[[str], None] = lambda _u: None
        self.on_question: Callable[[str, str], None] = lambda _u, _q: None
        self.bot_user_id = ""
        self.ignore_ids: frozenset[str] = frozenset()   # the player himself when his PC assistant listens
        self.owner_id: str = ""   # the player, also heard by his PC assistant: one answer (claim_wake)
        self.wakes = 0
        self.questions = 0
        self.refused = 0
        self.dropped_self = 0
        self.status = ""
        self.test_until = -1.0              # « Tester » on the Discord page: listen even outside a game
        self.on_test: Callable[[str, str], None] = lambda _u, _t: None
        self.views: dict[str, dict] = {}    # uid -> the member's « Écoute » line (worker thread writes)
        self._speak_since = -1.0
        self._speak_stuck_logged = False

    # -- any thread ---------------------------------------------------------------------------
    def testing(self) -> bool:
        return self._clock() < self.test_until

    def test(self, seconds: float = va.TEST_WINDOW_S * 1.5) -> None:
        """Listen for ``seconds`` even outside a game (the question is NOT sent to the AI)."""
        self.test_until = self._clock() + float(seconds)
        log.info("Discord écoute : test lancé (%.0f s)", seconds)

    def active(self) -> bool:
        if self.testing():
            return True
        try:
            return bool(self.should_listen())
        except Exception:
            return False

    def member_views(self) -> dict[str, dict]:
        """``{uid: {level, bar, heard, stage, age_s}}`` of the members heard so far. Any thread."""
        return {k: dict(v) for k, v in dict(self.views).items()}

    def push(self, uid: Any, pcm16: bytes) -> None:
        uid = str(uid or "")
        if not uid or not pcm16 or uid == self.bot_user_id or uid in self.ignore_ids:
            return
        try:
            self._q.put_nowait((uid, pcm16))
        except queue.Full:
            pass
        if self._threaded:
            self._ensure_thread()

    def answered(self) -> None:
        """The answer was said (or failed): the next question may come."""
        with self._lock:
            self._busy_until = -1e9

    def stop(self) -> None:
        self._stop.set()

    # -- worker ---------------------------------------------------------------------------------
    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="TreeAI-discord-listen", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        idle = 0
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=0.25)
            except queue.Empty:
                item = None
            try:
                self.step(item)
            except Exception:
                log.debug("Discord listen: step failed", exc_info=True)
            idle = idle + 1 if item is None else 0
            if idle > 240 and not self._speakers:     # a minute of nothing: the thread ends (restarts on push)
                return

    def _whisper(self) -> Any:
        """Local Whisper (zero request) once loaded; only the audio of a wake + its question reaches it."""
        if self._stt is not None:
            return self._stt
        if self._make_rec_fn is not None:
            return None
        try:
            a = va.assistant()
            return a._stt() if a.whisper_ready() else None
        except Exception:
            return None

    def _make_rec(self, grammar: Any) -> Any:
        try:
            if self._make_rec_fn is not None:
                return self._make_rec_fn(grammar)
            return va.assistant()._make_rec(grammar)    # the same (shared) Vosk model as the PC assistant
        except Exception:
            log.debug("Discord listen: recognizer unavailable", exc_info=True)
            return None

    def step(self, item: tuple[str, bytes] | None) -> None:
        """One unit of work (tests call it directly): feed a block, then run every speaker's timer."""
        events: list[tuple[str, str, str]] = []
        if not self.active():
            self._speakers.clear()
            self.views = {}
            return
        if item is not None:
            uid, pcm = item
            if self._bot_speaking():
                self.dropped_self += 1
                sp = self._speakers.get(uid)
                if sp is not None and sp.q_rec is None:
                    sp.engine.reset()
            else:
                sp = self._speakers.get(uid)
                if sp is None:
                    if len(self._speakers) >= MAX_SPEAKERS:
                        oldest = min(self._speakers.values(), key=lambda s: s.last_audio)
                        self._speakers.pop(oldest.uid, None)
                    sp = self._speakers[uid] = Speaker(uid, self._make_rec, self._clock, self._whisper)
                events += [(uid, k, t) for k, t in sp.feed(pcm)]
        for sp in list(self._speakers.values()):
            events += [(sp.uid, k, t) for k, t in sp.tick()]
        for uid, kind, text in events:
            self._event(uid, kind, text)
        now = self._clock()
        self.views = {sp.uid: sp.view(now) for sp in self._speakers.values()}

    def _bot_speaking(self) -> bool:
        """The bot plays (friends' blocks dropped: its voice in their headsets must not wake it). A gate
        stuck on for more than :data:`BOT_SPEAKING_MAX_S` is ignored: it must not deafen the listener."""
        try:
            busy = bool(self.bot_speaking())
        except Exception:
            busy = False
        now = self._clock()
        if not busy:
            self._speak_since = -1.0
            return False
        if self._speak_since < 0:
            self._speak_since = now
        if now - self._speak_since > BOT_SPEAKING_MAX_S:
            if not self._speak_stuck_logged:
                self._speak_stuck_logged = True
                log.warning("Discord écoute : le bot semble parler depuis %.0f s, écoute reprise", now - self._speak_since)
            return False
        return True

    def _admit(self, uid: str) -> bool:
        now = self._clock()
        with self._lock:
            while self._asked and now - self._asked[0] > QUESTIONS_WINDOW_S:
                self._asked.popleft()
            if now < self._busy_until or len(self._asked) >= QUESTIONS_MAX \
                    or now - self._last_q.get(uid, -1e9) < SPEAKER_COOLDOWN_S:
                return False
            self._asked.append(now)
            self._last_q[uid] = now
            self._busy_until = now + BUSY_MAX_S
            return True

    def _event(self, uid: str, kind: str, text: str) -> None:
        sp = self._speakers.get(uid)
        if kind == "wake":
            if sp is not None and self.testing():           # the test: no rate limit, no AI
                if sp.start_question():
                    self.wakes += 1
                    try:
                        self.on_wake(uid)
                    except Exception:
                        log.debug("Discord listen: on_wake failed", exc_info=True)
                return
            if sp is None or not self._admit(uid):
                self.refused += 1
                return
            if uid and uid == self.owner_id:
                from treeaicoach.voice_assistant import claim_wake

                if not claim_wake("discord"):            # his PC mic already answered this one
                    return
            if not sp.start_question():
                self.answered()
                return
            self.wakes += 1
            try:
                self.on_wake(uid)
            except Exception:
                log.debug("Discord listen: on_wake failed", exc_info=True)
            return
        if kind == "question":
            log.info("Discord écoute : question de %d mot(s)", len(text.split()))
            if self.testing():
                self.status = f"Test : entendu « {text} »" if text else "Test : « Hey coach » entendu, question vide."
                try:
                    self.on_test(uid, text)
                except Exception:
                    log.debug("Discord listen: on_test failed", exc_info=True)
                return
            if len(text.split()) < va.MIN_QUESTION_WORDS:
                self.status = "Question non comprise."
                self.answered()
                return
            self.questions += 1
            self.status = f"Question : « {text} »"
            try:
                self.on_question(uid, text)
            except Exception:
                log.debug("Discord listen: on_question failed", exc_info=True)
                self.answered()


# ------------------------------------------------------------------------------ discord.py glue
class Receiver:
    """Reads the voice socket of one discord.py voice client and feeds a :class:`VoiceListener`.

    ``attach(vc)`` after joining; ``on_ws(msg)`` gets the voice websocket messages (the hook).
    The socket callback runs on discord.py's reader thread: cheap work only, never raises."""

    SPEAKING, CLIENTS_CONNECT, CLIENT_DISCONNECT = 5, 11, 13
    GUESS_TRIES = 50                    # packets of an unknown SSRC tried against the DAVE senders

    def __init__(self, listener: VoiceListener) -> None:
        self.listener = listener
        self._ssrc_user: dict[int, int] = {}
        self._decoders: dict[int, Any] = {}
        self._resamplers: dict[int, Any] = {}     # per SSRC: 48 kHz stereo -> 16 kHz mono, low-passed
        self._vc: Any = None
        self._guess_left: dict[int, int] = {}
        self.users: set[int] = set()     # members the voice server says are connected (CLIENTS_CONNECT)
        #: other members of the channel (the bot sets it): an unknown SSRC with ONE unmapped candidate is his
        self.candidates: Callable[[], Any] = lambda: ()
        self.packets = 0
        self.decoded = 0
        self.undecryptable = 0
        self.unmapped = 0                # audio of an SSRC nobody claimed (SPEAKING not seen)
        self.bad_rtp = 0                 # transport decryption failed / not RTP
        self.opus_errors = 0
        self.guessed = 0
        self.last_error = ""

    def stats(self) -> dict:
        """Every stage of the chain, for the page and the diagnostics (no names, no ids)."""
        return {"packets": self.packets, "bad_rtp": self.bad_rtp, "unmapped": self.unmapped,
                "undecryptable": self.undecryptable, "opus_errors": self.opus_errors, "decoded": self.decoded,
                "mapped": len(self._ssrc_user), "guessed": self.guessed, "attached": self._vc is not None,
                "last_error": self.last_error}

    def on_ws(self, msg: Any) -> None:
        try:
            op, d = msg.get("op"), msg.get("d") or {}
            if op == self.SPEAKING and "ssrc" in d and "user_id" in d:
                if int(d["ssrc"]) not in self._ssrc_user:
                    log.info("Discord écoute : un membre associé à son flux audio")
                self._ssrc_user[int(d["ssrc"])] = int(d["user_id"])
            elif op == self.CLIENTS_CONNECT:
                for u in d.get("user_ids") or []:
                    self.users.add(int(u))
            elif op == self.CLIENT_DISCONNECT and "user_id" in d:
                uid = int(d["user_id"])
                self.users.discard(uid)
                for s in [s for s, u in self._ssrc_user.items() if u == uid]:
                    self._ssrc_user.pop(s, None)
                    self._decoders.pop(s, None)
                    self._resamplers.pop(s, None)
        except Exception:
            pass

    def attach(self, vc: Any) -> bool:
        try:
            if self._vc is vc:
                return True
            self.detach()
            vc._connection.add_socket_listener(self._on_packet)
            self._vc = vc
            log.info("Discord écoute : lecture du salon branchée")
            return True
        except Exception as exc:
            self.last_error = f"attach: {type(exc).__name__}"
            log.warning("Discord listen: attach failed", exc_info=True)
            return False

    def detach(self) -> None:
        vc, self._vc = self._vc, None
        if vc is not None:
            try:
                vc._connection.remove_socket_listener(self._on_packet)
            except Exception:
                pass
        self._decoders.clear()
        self._resamplers.clear()

    def _guess(self, ssrc: int, frame: bytes, conn: Any) -> int | None:
        """The sender of an SSRC whose SPEAKING message was missed (joined while he talked, message
        before the reader was ready): with DAVE only the right sender's key decrypts the frame; without
        DAVE, the one member of the channel not yet mapped. Bounded tries per SSRC. Never raises."""
        try:
            left = self._guess_left.get(ssrc, self.GUESS_TRIES)
            if left <= 0:
                return None
            self._guess_left[ssrc] = left - 1
            own = int(getattr(getattr(conn, "user", None), "id", 0) or 0)
            mapped = set(self._ssrc_user.values())
            session = conn.dave_session if getattr(conn, "dave_protocol_version", 0) else None
            if session is not None and getattr(session, "ready", False) and frame.endswith(DAVE_MAGIC):
                ids: set[int] = set(self.users)
                try:
                    ids |= {int(u) for u in session.get_user_ids()}
                except Exception:
                    pass
                for uid in sorted(ids - mapped - {own}):
                    if dave_decrypt(session, uid, frame) is not None:
                        return uid
                return None
            cands = {int(c) for c in (self.candidates() or ()) if str(c).isdigit()} - mapped - {own}
            try:
                cands -= {int(self.listener.bot_user_id)} if self.listener.bot_user_id else set()
            except Exception:
                pass
            return next(iter(cands)) if len(cands) == 1 else None
        except Exception:
            return None

    def _on_packet(self, packet: bytes) -> None:
        try:
            if not self.listener.active():
                return
            vc = self._vc
            if vc is None:
                return
            self.packets += 1
            conn = vc._connection
            got = rtp_payload(packet, conn.secret_key, conn.mode)
            if got is None:
                self.bad_rtp += 1
                return
            ssrc, _seq, frame = got
            if ssrc == conn.ssrc:
                return
            uid = self._ssrc_user.get(ssrc)
            if uid is None:
                uid = self._guess(ssrc, frame, conn)
                if uid is None:
                    self.unmapped += 1
                    return
                self._ssrc_user[ssrc] = uid
                self.guessed += 1
                log.info("Discord écoute : flux audio associé sans message SPEAKING")
            frame = dave_decrypt(conn.dave_session if conn.dave_protocol_version else None, uid, frame)
            if frame is None:
                self.undecryptable += 1
                return
            dec = self._decoders.get(ssrc)
            try:
                if dec is None:
                    from discord.opus import Decoder  # noqa: PLC0415

                    dec = self._decoders[ssrc] = Decoder()
                pcm48 = dec.decode(None if frame == OPUS_SILENCE else frame, fec=False)
            except Exception as exc:
                self.opus_errors += 1
                self.last_error = f"opus: {type(exc).__name__}"
                if self.opus_errors in (1, 100):
                    log.warning("Discord écoute : décodage Opus impossible (%s)", type(exc).__name__)
                return
            self.decoded += 1
            rs = self._resamplers.get(ssrc)
            if rs is None:
                rs = self._resamplers[ssrc] = va.Resampler(48000, 2)
            self.listener.push(uid, rs.process(pcm48))
        except Exception:
            log.debug("Discord listen: packet dropped", exc_info=True)
