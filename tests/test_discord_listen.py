"""« Hey coach » heard in the Discord voice channel (discord_listen.py): packets, DAVE, resampling,
per-speaker wake / question, rate limits, never the bot's own audio. Fakes only (no network)."""
from __future__ import annotations

import struct
from types import SimpleNamespace

import nacl.secret
import numpy as np
import pytest

from treeaicoach import discord_listen as dl
from treeaicoach import voice_assistant as va


@pytest.fixture(autouse=True)
def _fresh_wake_claims() -> None:
    """claim_wake() is process-wide: a Discord wake of another test must not mute this one."""
    va._last_wake.update(pc=-1e9, discord=-1e9)


KEY = bytes(range(32))


def _packet(opus: bytes, ssrc: int = 77, seq: int = 5, ext: bytes = b"", key: bytes = KEY, nonce: int = 9) -> bytes:
    """A voice packet as Discord sends it (aead_xchacha20_poly1305_rtpsize)."""
    first = 0x80 | (0x10 if ext else 0)
    header = struct.pack(">BBHII", first, 0x78, seq, 1234, ssrc)
    if ext:
        header += b"\xbe\xde" + struct.pack(">H", len(ext) // 4)
    n = struct.pack(">I", nonce)
    ct = nacl.secret.Aead(key).encrypt(ext + opus, header, n + b"\x00" * 20).ciphertext
    return header + ct + n


def test_rtp_payload_decrypts_transport_with_and_without_extension() -> None:
    frame = b"\x11\x22\x33" * 10
    assert dl.rtp_payload(_packet(frame), KEY) == (77, 5, frame)
    assert dl.rtp_payload(_packet(frame, ext=b"\x10\x01\x02\x03"), KEY) == (77, 5, frame)
    assert dl.rtp_payload(_packet(frame), bytes(32)) is None                 # wrong key
    assert dl.rtp_payload(_packet(frame), KEY, mode="xsalsa20_poly1305") is None
    rtcp = bytearray(_packet(frame))
    rtcp[1] = 201
    assert dl.rtp_payload(bytes(rtcp), KEY) is None
    assert dl.rtp_payload(b"\x00\x02" + bytes(72), KEY) is None              # IP discovery answer
    assert dl.rtp_payload(b"", KEY) is None


class _Dave:
    def __init__(self, ready: bool = True, fail: bool = False) -> None:
        self.ready, self.fail, self.calls = ready, fail, []

    def decrypt(self, uid, media, frame):  # noqa: ANN001, ANN201
        self.calls.append((uid, str(media)))
        if self.fail:
            raise ValueError("no key")
        return frame[:-2]


def test_dave_layer_removed_per_sender() -> None:
    enc = b"opus-data" + dl.DAVE_MAGIC
    s = _Dave()
    assert dl.dave_decrypt(s, 42, enc) == b"opus-data"
    assert s.calls == [(42, "MediaType.audio")]
    assert dl.dave_decrypt(None, 42, b"plain") == b"plain"                   # no DAVE session
    assert dl.dave_decrypt(_Dave(fail=True), 42, enc) is None               # undecryptable: dropped
    assert dl.dave_decrypt(_Dave(fail=True), 42, b"passthrough") == b"passthrough"
    assert dl.dave_decrypt(_Dave(ready=False), 42, enc) is None
    assert dl.dave_decrypt(s, 42, dl.OPUS_SILENCE) == dl.OPUS_SILENCE


def test_48k_stereo_to_16k_mono() -> None:
    st = np.zeros((960, 2), dtype="<i2")
    st[:, 0], st[:, 1] = 300, 600
    out = np.frombuffer(dl.to_16k_mono(st.tobytes()), dtype="<i2")
    assert len(out) == 320 and set(out.tolist()) == {450}
    assert dl.to_16k_mono(b"\x01") == b""


# ------------------------------------------------------------------------------ the listener
WAKE, WORDS, NOISE = 1000, 2000, 50


def _block(v: int, s: float = 0.25) -> bytes:
    return np.full(int(16000 * s), v, dtype="<i2").tobytes()


class FakeRec:
    def __init__(self, grammar) -> None:  # noqa: ANN001
        self.wake = grammar is not None
        self._p = self._r = ""

    def accept(self, pcm: bytes) -> bool:
        v = int(np.frombuffer(pcm, dtype="<i2")[0])
        if self.wake:
            self._r = "hé coach" if v == WAKE else ""
            return v == WAKE
        if v == WORDS:
            self._r = "quel item acheter"
            return True
        return False

    def result(self) -> str:
        r, self._r = self._r, ""
        return r

    def partial(self) -> str:
        return self._p

    def final(self) -> str:
        return ""

    def reset(self) -> None:
        self._p = ""


def _listener(clock):  # noqa: ANN001, ANN202
    lst = dl.VoiceListener(make_rec=FakeRec, clock=lambda: clock[0], threaded=False)
    lst.should_listen = lambda: True
    ev = SimpleNamespace(wakes=[], questions=[])
    lst.on_wake = ev.wakes.append
    lst.on_question = lambda u, q: ev.questions.append((u, q))
    lst.bot_user_id = "999"
    return lst, ev


def test_any_member_wakes_then_question_is_delivered() -> None:
    clock = [100.0]
    lst, ev = _listener(clock)
    lst.step(("11", _block(NOISE)))
    assert ev.wakes == []
    lst.step(("11", _block(WAKE)))
    assert ev.wakes == ["11"]
    clock[0] += 0.5
    lst.step(("11", _block(WORDS)))
    assert ev.questions == [("11", "quel item acheter")]
    # another member, while the first answer is pending: refused until answered
    lst.step(("22", _block(WAKE)))
    assert ev.wakes == ["11"] and lst.refused == 1
    lst.answered()
    clock[0] += 1.0
    lst.step(("22", _block(WAKE)))
    assert ev.wakes == ["11", "22"]


def test_question_ends_on_silence_and_short_ones_are_dropped() -> None:
    clock = [0.0]
    lst, ev = _listener(clock)
    lst.step(("11", _block(WAKE)))
    assert ev.wakes == ["11"]
    clock[0] += va.QUESTION_START_S + 0.1   # nothing said after the chime (no packets at all): given up
    lst.step(None)
    assert ev.questions == [] and lst.status == "Question non comprise."


def test_never_the_bot_itself_nor_while_it_speaks_nor_when_off() -> None:
    clock = [0.0]
    lst, ev = _listener(clock)
    lst.push("999", _block(WAKE))       # the bot's own user id
    assert lst._q.empty()
    speaking = [True]
    lst.bot_speaking = lambda: speaking[0]
    lst.step(("11", _block(WAKE)))
    assert ev.wakes == [] and lst.dropped_self == 1
    speaking[0] = False
    lst.should_listen = lambda: False
    lst.step(("11", _block(WAKE)))
    assert ev.wakes == []


def test_rate_limits_per_speaker_and_overall() -> None:
    clock = [0.0]
    lst, ev = _listener(clock)
    for _ in range(2):
        lst.step(("11", _block(WAKE)))
        lst.step(("11", _block(WORDS)))
        lst.answered()
        clock[0] += 5.0
    assert len(ev.questions) == 1        # same person within 20 s: once
    for i in range(dl.QUESTIONS_MAX + 3):
        clock[0] += dl.SPEAKER_COOLDOWN_S + 1
        lst.step((str(100 + i), _block(WAKE)))
        lst.step((str(100 + i), _block(WORDS)))
        lst.answered()
    assert len(ev.questions) <= dl.QUESTIONS_MAX


def test_receiver_maps_ssrc_and_drops_own_audio(monkeypatch) -> None:  # noqa: ANN001
    import discord.opus

    class Dec:
        def decode(self, data, *, fec):  # noqa: ANN001, ANN201
            return np.full(1920, 900, dtype="<i2").tobytes()

    monkeypatch.setattr(discord.opus, "Decoder", Dec)
    got = []
    lst = SimpleNamespace(active=lambda: True, push=lambda u, p: got.append((u, len(p))))
    rx = dl.Receiver(lst)  # type: ignore[arg-type]
    conn = SimpleNamespace(secret_key=list(KEY), mode="aead_xchacha20_poly1305_rtpsize", ssrc=1,
                           dave_session=None, dave_protocol_version=0,
                           add_socket_listener=lambda cb: None, remove_socket_listener=lambda cb: None)
    assert rx.attach(SimpleNamespace(_connection=conn))
    rx._on_packet(_packet(b"abc" * 5, ssrc=77))
    assert got == []                                   # SSRC not known yet
    rx.on_ws({"op": 5, "d": {"user_id": "42", "ssrc": 77, "speaking": 1}})
    rx._on_packet(_packet(b"abc" * 5, ssrc=77))
    assert got == [(42, 640)]                          # 20 ms at 16 kHz mono
    rx.on_ws({"op": 5, "d": {"user_id": "7", "ssrc": 1, "speaking": 1}})
    rx._on_packet(_packet(b"abc" * 5, ssrc=1))         # the bot's own SSRC
    assert len(got) == 1
    rx.on_ws({"op": 13, "d": {"user_id": "42"}})
    rx._on_packet(_packet(b"abc" * 5, ssrc=77))
    assert len(got) == 1
    rx._on_packet(b"garbage")                          # never raises
