"""« Hey Tree AI » heard in the Discord voice channel (off by default: ``discord_listen``).

discord.py 2.7 has no voice *receive* API, but everything needed is there without a new
dependency: the voice UDP socket can be read (``VoiceConnectionState.add_socket_listener``), the voice
websocket messages can be watched (the ``hook`` of the connection state: SPEAKING gives the
SSRC -> user id map), PyNaCl decrypts the transport (``aead_xchacha20_poly1305_rtpsize``, the mode
discord.py picks), davey's ``DaveSession.decrypt(user_id, MediaType.audio, frame)`` removes the DAVE
end-to-end layer (the bot is a member of the channel's MLS group, so it holds every sender's key) and
``discord.opus.Decoder`` (libopus already shipped for speaking) gives 48 kHz stereo PCM.
(discord-ext-voice-recv, the usual add-on, was last released in June 2025 and knows nothing of DAVE,
mandatory since March 2026: it would hand DAVE ciphertext to the Opus decoder.)

Per speaker: 48 kHz stereo -> 16 kHz mono, then the SAME logic as the PC assistant
(voice_assistant: Vosk wake grammar + ``is_wake``, then a free recognizer for the question, ≤ 6 s,
ends on 1.2 s of silence). Packets stop while someone is silent, so a ticker ends questions on time.

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
QUEUE_MAX = 400                          # 16 kHz blocks waiting for the recognizers (~8 s of 1 speaker)
MAX_SPEAKERS = 8


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
    """« Hey Tree AI » then the question, for ONE member of the channel (fed 16 kHz mono blocks)."""

    def __init__(self, uid: str, make_rec: Callable[[Any], Any], clock: Callable[[], float]) -> None:
        self.uid = uid
        self._make_rec = make_rec
        self._clock = clock
        self._buf = bytearray()
        self.wake_rec: Any = None
        self.q_rec: Any = None              # set while a question is being recorded
        self.q_start = 0.0
        self.q_change = 0.0
        self.q_heard = False
        self.q_parts: list[str] = []
        self.q_partial = ""
        self.last_audio = -1e9

    def feed(self, pcm16: bytes) -> list[tuple[str, str]]:
        """Events: ``("wake", "")`` / ``("question", text)``."""
        self.last_audio = self._clock()
        self._buf += pcm16
        out: list[tuple[str, str]] = []
        while len(self._buf) >= BLOCK_BYTES:
            block, self._buf = bytes(self._buf[:BLOCK_BYTES]), self._buf[BLOCK_BYTES:]
            out += self._block(block)
        return out

    def _block(self, block: bytes) -> list[tuple[str, str]]:
        if self.q_rec is not None:
            rec = self.q_rec
            if rec.accept(block):
                txt = rec.result()
                if txt:
                    self.q_parts.append(txt)
                    self.q_heard, self.q_change = True, self._clock()
                    if len(" ".join(self.q_parts).split()) >= va.MIN_QUESTION_WORDS:
                        return self._end()
                self.q_partial = ""
            else:
                p = rec.partial()
                if p and p != self.q_partial:
                    self.q_partial, self.q_heard, self.q_change = p, True, self._clock()
            return self.tick()
        if self.wake_rec is None:
            self.wake_rec = self._make_rec(va.WAKE_GRAMMAR)
            if self.wake_rec is None:
                return []
        heard = self.wake_rec.result() if self.wake_rec.accept(block) else self.wake_rec.partial()
        if heard and va.is_wake(heard):
            self.wake_rec.reset()
            return [("wake", "")]
        return []

    def start_question(self) -> bool:
        rec = self._make_rec(None)
        if rec is None:
            return False
        self.q_rec, self.q_start = rec, self._clock()
        self.q_change, self.q_heard, self.q_parts, self.q_partial = self.q_start, False, [], ""
        self._buf.clear()                   # what was said with the wake phrase is not the question
        return True

    def tick(self) -> list[tuple[str, str]]:
        now = self._clock()
        if self.q_rec is None:
            if self.wake_rec is not None and now - self.last_audio >= WAKE_RESET_SILENCE_S and self.last_audio > 0:
                self.wake_rec.reset()
                self.last_audio = 0.0
            return []
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
        if self.wake_rec is not None:
            self.wake_rec.reset()
        return [("question", q)]


# ------------------------------------------------------------------------------ the channel
class VoiceListener:
    """Every speaker of the channel. ``push(user_id, pcm16)`` from the socket thread (cheap: a queue);
    a worker thread runs the recognizers and the timers.

    Hooks: ``should_listen()`` (switch on + in game), ``bot_speaking()`` (the bot plays: blocks
    dropped), ``on_wake(uid)`` (chime in the channel), ``on_question(uid, text)`` (ask the AI)."""

    def __init__(self, *, make_rec: Callable[[Any], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic, threaded: bool = True) -> None:
        self._make_rec_fn = make_rec
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
        self.wakes = 0
        self.questions = 0
        self.refused = 0
        self.dropped_self = 0
        self.status = ""

    # -- any thread ---------------------------------------------------------------------------
    def active(self) -> bool:
        try:
            return bool(self.should_listen())
        except Exception:
            return False

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
            return
        if item is not None:
            uid, pcm = item
            try:
                speaking = bool(self.bot_speaking())
            except Exception:
                speaking = False
            if speaking:
                self.dropped_self += 1
                sp = self._speakers.get(uid)
                if sp is not None and sp.q_rec is None and sp.wake_rec is not None:
                    sp.wake_rec.reset()
            else:
                sp = self._speakers.get(uid)
                if sp is None:
                    if len(self._speakers) >= MAX_SPEAKERS:
                        oldest = min(self._speakers.values(), key=lambda s: s.last_audio)
                        self._speakers.pop(oldest.uid, None)
                    sp = self._speakers[uid] = Speaker(uid, self._make_rec, self._clock)
                events += [(uid, k, t) for k, t in sp.feed(pcm)]
        for sp in list(self._speakers.values()):
            events += [(sp.uid, k, t) for k, t in sp.tick()]
        for uid, kind, text in events:
            self._event(uid, kind, text)

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
            if sp is None or not self._admit(uid):
                self.refused += 1
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

    def __init__(self, listener: VoiceListener) -> None:
        self.listener = listener
        self._ssrc_user: dict[int, int] = {}
        self._decoders: dict[int, Any] = {}
        self._vc: Any = None
        self.packets = 0
        self.decoded = 0
        self.undecryptable = 0

    def on_ws(self, msg: Any) -> None:
        try:
            op, d = msg.get("op"), msg.get("d") or {}
            if op == self.SPEAKING and "ssrc" in d and "user_id" in d:
                self._ssrc_user[int(d["ssrc"])] = int(d["user_id"])
            elif op == self.CLIENT_DISCONNECT and "user_id" in d:
                uid = int(d["user_id"])
                for s in [s for s, u in self._ssrc_user.items() if u == uid]:
                    self._ssrc_user.pop(s, None)
                    self._decoders.pop(s, None)
        except Exception:
            pass

    def attach(self, vc: Any) -> bool:
        try:
            if self._vc is vc:
                return True
            self.detach()
            vc._connection.add_socket_listener(self._on_packet)
            self._vc = vc
            return True
        except Exception:
            log.debug("Discord listen: attach failed", exc_info=True)
            return False

    def detach(self) -> None:
        vc, self._vc = self._vc, None
        if vc is not None:
            try:
                vc._connection.remove_socket_listener(self._on_packet)
            except Exception:
                pass
        self._decoders.clear()

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
                return
            ssrc, _seq, frame = got
            uid = self._ssrc_user.get(ssrc)
            if uid is None or ssrc == conn.ssrc:
                return
            frame = dave_decrypt(conn.dave_session if conn.dave_protocol_version else None, uid, frame)
            if frame is None:
                self.undecryptable += 1
                return
            dec = self._decoders.get(ssrc)
            if dec is None:
                from discord.opus import Decoder  # noqa: PLC0415

                dec = self._decoders[ssrc] = Decoder()
            pcm48 = dec.decode(None if frame == OPUS_SILENCE else frame, fec=False)
            self.decoded += 1
            self.listener.push(uid, to_16k_mono(pcm48))
        except Exception:
            log.debug("Discord listen: packet dropped", exc_info=True)
