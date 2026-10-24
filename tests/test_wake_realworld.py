"""« Hey coach » on real-world audio (2.19 field report: never triggers, neither from the PC mic nor from
friends in Discord): what the French model writes for the wake phrase, the free recognizer next to the
grammar one, comfort noise when Discord stops sending packets, the streaming 48 kHz stereo -> 16 kHz
resampler (no aliasing, no click at block borders), the quiet-mic gain, the « Test micro 5 s ». Fakes only."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace as NS

import numpy as np
import pytest

from treeaicoach import discord_listen as dl
from treeaicoach import voice_assistant as va


@pytest.fixture(autouse=True)
def _fresh_wake_claims() -> None:
    """claim_wake() is process-wide: a Discord wake of another test must not mute this one."""
    va._last_wake.update(pc=-1e9, discord=-1e9)


BLOCK = va.BLOCK_FRAMES


# ------------------------------------------------------------------------------ what the model writes
@pytest.mark.parametrize("text,kind", [
    ("et coach quel objet", "strong"), ("hé coach", "strong"), ("oh coach on fait quoi", "strong"),
    ("tri coach", "strong"), ("hey tree coach", "strong"), ("bon et coachs", "strong"),
    ("coach il est où le jungler", "bare"),
])
def test_wake_variants_any_recognizer(text: str, kind: str) -> None:
    assert va.wake_kind(text) == kind


@pytest.mark.parametrize("text", ["et cocher", "hey coche", "hé kotch", "hey tri", "et caouche"])
def test_weak_variants_only_alone_and_sure(text: str) -> None:
    assert va.wake_kind(text, [0.9, 0.9], final=True) in ("weak", "strong")
    assert va.wake_kind(text + " la case en bas", [0.9] * 5, final=True) in ("", "strong")
    assert va.wake_kind(text, [0.3, 0.9], final=True) in ("", "strong")


@pytest.mark.parametrize("text", ["le coach m'a dit de reculer", "il faut cocher la case", "ce coach est pratique",
                                  "mon coach de foot", "et toi tu joues quoi", "et tri", "tri tri",
                                  "c'est cauchemardesque", "la couche d'ozone", "le coq chante"])
def test_ordinary_talk_is_not_a_wake(text: str) -> None:
    assert va.wake_kind(text, [0.95] * len(text.split()), final=True) == ""
    assert va.wake_kind(text) == ""


def test_strip_wake_bare_and_tree() -> None:
    assert va.strip_wake("Coach, il est où le jungler") == "il est où le jungler"
    assert va.strip_wake("tri coach on fait quoi") == "on fait quoi"
    assert va.question_text(["et coach"], "quel objet") == "quel objet"
    assert va.question_text(["hé coach"]) == ""


# ------------------------------------------------------------------------------ audio helpers
def test_comfort_noise_is_quiet_and_sized() -> None:
    n = va.comfort_noise(0.75)
    assert len(n) == int(16000 * 0.75) * 2
    assert 1.0 < va.block_rms(n) < 6.0
    assert va.comfort_noise(0) == b""


def _tone(rate: int, seconds: float, freq: float, amp: float, channels: int = 1) -> bytes:
    t = np.arange(int(rate * seconds)) / rate
    x = amp * np.sin(2 * np.pi * freq * t)
    return np.repeat(x[:, None], channels, axis=1).astype("<i2").tobytes()


@pytest.mark.parametrize("rate,channels,chunk_s", [(48000, 2, 0.02), (48000, 1, 0.25), (44100, 1, 0.25),
                                                   (44100, 2, 0.02), (32000, 1, 0.25)])
def test_streaming_resampler_keeps_speech_and_drops_aliases(rate: int, channels: int, chunk_s: float) -> None:
    pcm = _tone(rate, 2.0, 440.0, 8000, channels)
    rs = va.Resampler(rate, channels)
    step = int(rate * chunk_s) * 2 * channels
    out = np.frombuffer(b"".join(rs.process(pcm[i:i + step]) for i in range(0, len(pcm), step)), "<i2")
    assert abs(out.size - 32000) <= 2
    y = out[200:].astype(float)
    assert abs(np.sqrt(np.mean(y * y)) - 8000 / np.sqrt(2)) < 120           # 440 Hz kept (no loss)
    spec = np.abs(np.fft.rfft(y * np.hanning(y.size)))
    assert abs(np.argmax(spec) * 16000 / y.size - 440) < 3                  # at the right pitch
    d = np.diff(y)
    assert np.max(np.abs(d)) < 8000 * 2 * np.pi * 440 / 16000 * 1.1         # no click at block borders
    # an 11 kHz hiss (above the 8 kHz of the recognizer) must not fold back into the speech band
    hiss = np.frombuffer(va.Resampler(rate, channels).process(_tone(rate, 1.0, 11000.0, 8000, channels)), "<i2")
    assert va.block_rms(hiss[200:].tobytes()) < 50
    old = np.frombuffer(va.resample_to_16k(_tone(rate, 1.0, 11000.0, 8000, channels), rate, channels), "<i2")
    assert va.block_rms(old[200:].tobytes()) > 1000                         # (what the old resampler let through)


def test_resampler_never_raises() -> None:
    rs = va.Resampler(48000, 2)
    assert rs.process(b"") == b"" and rs.process(b"\x01") == b""
    assert va.Resampler(16000, 1).process(b"\x01\x00\x02\x00") == b"\x01\x00\x02\x00"


def test_agc_lifts_a_very_quiet_mic_only() -> None:
    agc = va.Agc()
    quiet = np.full(BLOCK, 3, "<i2").tobytes()
    speech = (np.sin(np.arange(BLOCK) / 5) * 150).astype("<i2").tobytes()          # ~ -50 dBFS speech
    for _ in range(4):
        agc.process(quiet)
    for _ in range(20):                                                            # ~4 s to reach x8
        out = agc.process(speech)
    assert agc.gain == pytest.approx(va.AGC_MAX_GAIN)
    assert va.block_rms(out) == pytest.approx(va.block_rms(speech) * va.AGC_MAX_GAIN, rel=0.05)
    loud = (np.sin(np.arange(BLOCK) / 5) * 4000).astype("<i2").tobytes()
    agc2 = va.Agc()
    assert all(agc2.process(loud) == loud for _ in range(6)) and agc2.gain == 1.0   # a normal mic: untouched
    assert agc.process(loud) == loud or agc.gain < va.AGC_MAX_GAIN                  # gain falls at once
    assert va.Agc().process(b"") == b""


# ------------------------------------------------------------------------------ the engine
class Scripted:
    """Recognizer replaying (kind, text, confs) per accepted block; ``endpoint``: it only ENDS an
    utterance (final result) after ``endpoint`` quiet blocks, like Kaldi's trailing-silence rule."""

    def __init__(self, items=None, *, endpoint: int = 0, final_text: str = "") -> None:  # noqa: ANN001
        self.items, self.i = list(items or []), -1
        self.endpoint, self.final_text = endpoint, final_text
        self.last_raw, self.last_confs = "", None
        self._loud = self._quiet = 0
        self.resets = 0

    def accept(self, pcm: bytes) -> bool:
        if self.endpoint:
            if va.block_rms(pcm) > 100:
                self._loud, self._quiet = self._loud + 1, 0
                self.last_raw = ""
                return False
            self._quiet += 1
            if self._loud and self._quiet >= self.endpoint:
                self._loud = 0
                self.last_raw, self.last_confs = self.final_text, [0.9] * len(self.final_text.split())
                return True
            return False
        self.i += 1
        k, t, c = self.items[self.i] if self.i < len(self.items) else ("P", "", None)
        self.last_raw, self.last_confs = t, c
        return k == "F"

    def result(self) -> str:
        return self.last_raw.replace("[unk]", "").strip()

    def partial(self) -> str:
        return "" if self.endpoint else self.result()

    def final(self) -> str:
        return ""

    def reset(self) -> None:
        self.resets += 1
        self._loud = self._quiet = 0


LOUD = np.full(BLOCK, 3000, "<i2").tobytes()
QUIET = bytes(BLOCK * 2)


def test_free_recognizer_wakes_when_the_grammar_one_does_not() -> None:
    gram = Scripted([("P", "[unk]", None)] * 6)                      # the grammar one hears nothing useful
    free = Scripted([("P", "et coach", None), ("P", "et coach quel", None)])     # fed speech blocks only
    eng = va.WakeEngine(lambda g: gram if g else free)
    got = [eng.feed(b) for b in [QUIET, QUIET, LOUD, LOUD]]
    assert got == [False, False, False, True] and eng.last_source == "libre"
    rec, carry, partial = eng.take_question()                         # mid-utterance: the question goes on in it
    assert rec is free and carry == [] and partial == "et coach quel"
    assert va.question_text([], partial) == "quel"
    assert eng.take_question() == (None, [], "")                     # one shot
    assert eng.free_rec is None and eng.ready() is True               # a fresh free recognizer next time


def test_wake_and_question_in_one_final_result_is_carried() -> None:
    gram = Scripted([("P", "", None)] * 3)
    free = Scripted([("F", "hé coach quel objet j'achète", [0.9] * 5)])
    eng = va.WakeEngine(lambda g: gram if g else free)
    assert [eng.feed(b) for b in [QUIET, QUIET, LOUD]] == [False, False, True]
    assert eng.take_question() == (None, ["hé coach quel objet j'achète"], "")


def test_engine_without_free_recognizer_and_errors() -> None:
    gram = Scripted([("F", "hey coach", [1, 1])])
    eng = va.WakeEngine(lambda g: gram if g else None)
    assert eng.feed(LOUD) is True and eng.last_source == "grammaire"
    eng2 = va.WakeEngine(lambda g: None)
    assert eng2.feed(LOUD) is False and eng2.flush() is False


def test_flush_ends_an_utterance_whose_audio_stopped() -> None:
    gram = Scripted(endpoint=2, final_text="hé coach")
    eng = va.WakeEngine(lambda g: gram if g else None)
    assert not any(eng.feed(LOUD) for _ in range(3))                  # « hé coach »… then the packets stop
    assert eng.flush() is True                                        # comfort noise: the final comes out


# ------------------------------------------------------------------------------ Discord: VAD-gated audio
def _vad_listener(clock, final_text: str = "hé coach"):  # noqa: ANN001, ANN202
    made: list[Scripted] = []

    def make(g):  # noqa: ANN001, ANN202
        if g is None:
            return Scripted([("P", "", None)] * 50)
        r = Scripted(endpoint=3, final_text=final_text)
        made.append(r)
        return r
    lst = dl.VoiceListener(make_rec=make, clock=lambda: clock[0], threaded=False)
    lst.should_listen = lambda: True
    wakes: list[str] = []
    lst.on_wake = wakes.append
    return lst, wakes


def _talk(lst, clock, uid: str, seconds: float, amp: int = 3000) -> None:  # noqa: ANN001
    """Discord: one 20 ms packet (48 kHz stereo, through the per-SSRC resampler) every 20 ms, only while talking."""
    rs = va.Resampler(48000, 2)
    for _ in range(int(seconds / 0.02)):
        clock[0] += 0.02
        pcm = np.full((960, 2), amp, "<i2").tobytes()
        lst.step((uid, rs.process(pcm)))


def test_discord_wake_said_then_silence_fires_without_more_packets() -> None:
    clock = [100.0]
    lst, wakes = _vad_listener(clock)
    _talk(lst, clock, "7", 0.8)                                       # « Hey coach »… and nothing more is sent
    assert wakes == []
    for _ in range(4):                                                # the worker ticks while nobody sends
        clock[0] += 0.25
        lst.step(None)
    assert wakes == ["7"]                                             # before: never (no final, then reset)


def test_discord_silence_flush_does_not_wake_on_ordinary_talk() -> None:
    clock = [100.0]
    lst, wakes = _vad_listener(clock, final_text="le coach m'a dit")
    for _ in range(3):
        _talk(lst, clock, "7", 1.0)
        for _ in range(8):
            clock[0] += 0.25
            lst.step(None)
    assert wakes == []


def test_discord_stuck_bot_voice_gate_does_not_deafen() -> None:
    clock = [100.0]
    lst, wakes = _vad_listener(clock)
    lst.bot_speaking = lambda: True
    _talk(lst, clock, "7", 0.8)
    assert lst.dropped_self > 0
    clock[0] += dl.BOT_SPEAKING_MAX_S + 1
    _talk(lst, clock, "7", 0.8)
    for _ in range(4):
        clock[0] += 0.25
        lst.step(None)
    assert wakes == ["7"]


def test_discord_question_said_in_the_same_breath_is_kept() -> None:
    clock = [0.0]
    free_items = [("P", "et coach", None), ("P", "et coach quel objet", None)]

    def make(g):  # noqa: ANN001, ANN202
        return Scripted([("P", "[unk]", None)] * 20) if g else Scripted(free_items + [("F", "et coach quel objet acheter", [1] * 5)])
    lst = dl.VoiceListener(make_rec=make, clock=lambda: clock[0], threaded=False)
    lst.should_listen = lambda: True
    asked: list[tuple[str, str]] = []
    lst.on_question = lambda u, q: asked.append((u, q))
    for b in (QUIET, LOUD, LOUD, LOUD):
        clock[0] += 0.25
        lst.step(("5", b))
    assert lst.wakes == 1
    assert asked == [("5", "quel objet acheter")]


# ------------------------------------------------------------------------------ « Test micro 5 s »
class _Mic:
    def __init__(self, blocks: list[bytes]) -> None:
        self.blocks, self.closed = list(blocks), False

    def read(self) -> bytes | None:
        return self.blocks.pop(0) if self.blocks else QUIET

    def close(self) -> None:
        self.closed = True


class _FreeRec(Scripted):
    def __init__(self, _g) -> None:  # noqa: ANN001
        super().__init__([("P", "bonjour", None), ("F", "bonjour coach tu m'entends", [1] * 4)])

    def final(self) -> str:
        return ""


def _check(blocks: list[bytes], rec=_FreeRec) -> tuple[str, bool, _Mic]:  # noqa: ANN001
    mic = _Mic(blocks)
    a = va.VoiceAssistant(mic_factory=lambda _n: mic, recognizer_factory=rec)
    got: list[tuple[str, bool]] = []
    ev = threading.Event()
    assert a.mic_check(lambda m, ok: (got.append((m, ok)), ev.set()), seconds=1.0)
    assert ev.wait(3.0)
    return got[0][0], got[0][1], mic


def test_mic_check_shows_the_transcript_and_level() -> None:
    msg, ok, mic = _check([LOUD, LOUD, QUIET, QUIET])
    assert ok and "« bonjour coach tu m'entends »" in msg and "dB" in msg and mic.closed


def test_mic_check_reports_a_dead_or_very_quiet_mic() -> None:
    msg, ok, _ = _check([QUIET] * 4)
    assert not ok and "aucun son" in msg and "Confidentialité" in msg
    faint = np.full(BLOCK, 60, "<i2").tobytes()
    msg, ok, _ = _check([faint] * 4, rec=lambda g: Scripted([]))
    assert not ok and "aucun mot" in msg and "très faible" in msg


def test_mic_check_runs_once_at_a_time_and_panel_shows_it() -> None:
    gate = threading.Event()

    class SlowMic(_Mic):
        def read(self) -> bytes | None:
            gate.wait(2.0)
            return LOUD
    a = va.VoiceAssistant(mic_factory=lambda _n: SlowMic([]), recognizer_factory=_FreeRec)
    done = threading.Event()
    assert a.mic_check(lambda m, ok: done.set(), seconds=0.5)
    assert a.mic_check(None) is False
    end = time.monotonic() + 2
    while not a.snapshot()["mic_checking"] and time.monotonic() < end:
        time.sleep(0.01)
    snap = a.snapshot()
    assert snap["mic_checking"] and snap["listening"]
    from treeaicoach.ui_page_alerts import listen_line
    assert listen_line(snap).startswith("test micro")
    gate.set()
    assert done.wait(3.0)


def test_discord_chain_line() -> None:
    from treeaicoach.ui_page_discord import listen_chain_text
    assert listen_chain_text(None) == "" and listen_chain_text({"stats": {"attached": False}}) == ""
    line = listen_chain_text({"stats": {"attached": True, "packets": 1500, "decoded": 1400, "undecryptable": 90,
                                        "unmapped": 10}, "status": ""})
    assert "1 500 paquets" in line and "90 chiffrés (DAVE) illisibles" in line and "10 sans membre" in line
    assert "aucun paquet" in listen_chain_text({"stats": {"attached": True, "packets": 0}})


def test_model_path_is_unchanged_off_windows(tmp_path) -> None:  # noqa: ANN001
    assert va.model_path_for_engine(tmp_path / "modèle") == str(tmp_path / "modèle")


def test_assistant_uses_the_engine_end_to_end() -> None:
    """PC loop: the free recognizer alone hears « et coach quel objet » in one breath -> asked."""
    class Rec(Scripted):
        def __init__(self, g) -> None:  # noqa: ANN001
            super().__init__()
            self.g = g

        def accept(self, pcm: bytes) -> bool:
            txt = pcm.rstrip(b"\0").decode(errors="ignore")
            if self.g:                                   # grammar: never sure
                self.last_raw = "[unk]"
                return False
            if txt.startswith("F:"):
                self.last_raw, self.last_confs = txt[2:], [1.0] * len(txt[2:].split())
                return True
            self.last_raw = txt[2:] if txt.startswith("P:") else ""
            return False

    def blk(t: str) -> bytes:
        return t.encode().ljust(BLOCK * 2, b"\0")
    script = [QUIET, QUIET, blk("P:et coach"), blk("P:et coach quel"), blk("F:et coach quel objet acheter")]
    t = [0.0]

    class Mic(_Mic):
        def read(self) -> bytes | None:
            t[0] += va.BLOCK_S
            time.sleep(0.001)
            return super().read()
    a = va.VoiceAssistant(mic_factory=lambda _n: Mic(script), recognizer_factory=Rec, clock=lambda: t[0])
    asked: list[str] = []
    a.set_hooks(should_listen=lambda: True, on_question=asked.append)
    a.configure(NS(voice_assistant=True))
    try:
        end = time.monotonic() + 3
        while not asked and time.monotonic() < end:
            time.sleep(0.01)
        assert asked == ["quel objet acheter"] and a.wakes == 1
    finally:
        a.stop()


def test_free_recognizer_idles_in_silence() -> None:
    """CPU: the free recognizer (~16 % of a core when fed) only hears speech and the 1 s after it."""
    free = Scripted([])
    eng = va.WakeEngine(lambda g: Scripted([]) if g else free)
    for _ in range(20):
        eng.feed(QUIET)
    assert free.i == -1
    eng.feed(LOUD)
    for _ in range(10):
        eng.feed(QUIET)
    assert free.i + 1 == va.FREE_TAIL_BLOCKS                       # the loud block + 0.75 s after it
