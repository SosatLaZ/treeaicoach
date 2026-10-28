"""Push-to-talk held key (no VAD cut mid-sentence, bips on press and end) and League-name correction."""

from __future__ import annotations

import wave

import numpy as np
import pytest

from treeaicoach import stt_local
from treeaicoach import voice_assistant as va
from treeaicoach.paths import asset_path

BLOCK = va.BLOCK_FRAMES


@pytest.fixture(autouse=True)
def _fresh_wake_claims() -> None:
    va._last_wake.update(pc=-1e9, discord=-1e9)


def _clip() -> bytes:
    with wave.open(str(asset_path("sounds", "voice", "baron_dans_30_secondes.wav"))) as w:
        return va.resample_to_16k(w.readframes(w.getnframes()), w.getframerate(), w.getnchannels())


def _silence(s: float) -> bytes:
    return np.random.default_rng(0).normal(0, 20, int(16000 * s)).astype("<i2").tobytes()


def _blocks(pcm: bytes) -> list[bytes]:
    n = BLOCK * 2
    pcm += b"\0" * (-len(pcm) % n)
    return [pcm[i:i + n] for i in range(0, len(pcm), n)]


class _ClockMic:
    """Each read = 0.25 s of the fake clock."""

    def __init__(self, blocks: list[bytes], clock: list[float]) -> None:
        self.blocks, self.clock, self.reads = list(blocks), clock, 0

    def read(self) -> bytes:
        self.clock[0] += va.BLOCK_S
        self.reads += 1
        return self.blocks.pop(0) if self.blocks else _silence(va.BLOCK_S)

    def close(self) -> None:
        pass


class _STT:
    def __init__(self, text: str) -> None:
        self.text, self.calls, self.status = text, [], "prêt"

    def ready(self) -> bool:
        return True

    def transcribe(self, pcm: bytes, prompt=None) -> str:  # noqa: ANN001
        self.calls.append(len(pcm))
        return self.text


def _assistant(stt: _STT, clock: list[float]) -> tuple[va.VoiceAssistant, list[str], list[str]]:
    a = va.VoiceAssistant(mic_factory=lambda n: None, recognizer_factory=lambda g: None, stt=stt,
                          clock=lambda: clock[0])
    asked: list[str] = []
    bips: list[str] = []
    a.on_question = asked.append
    a.on_wake = lambda: bips.append("press")
    a.on_ptt_end = lambda: bips.append("end")
    return a, asked, bips


def test_ptt_records_the_whole_held_time_despite_pauses() -> None:
    clock = [0.0]
    stt = _STT("quel objet je fais contre Zed")
    a, asked, bips = _assistant(stt, clock)
    # speech, a 1.5 s pause (the VAD would cut at 1.0 s), speech again, all while the key is held (6 s)
    audio = _clip() + _silence(1.5) + _clip()
    mic = _ClockMic(_blocks(audio), clock)
    a.key_down = lambda: clock[0] < 6.0
    a._question_ptt(mic)
    assert asked == ["quel objet je fais contre Zed"] and bips == ["press", "end"]
    assert len(stt.calls) == 1 and stt.calls[0] >= len(audio) - 2 * BLOCK * 2
    # ended about PTT_TAIL_S after the release (6 s), not earlier
    assert 6.0 + va.PTT_TAIL_S - 0.3 <= a.ptt_seconds <= 6.0 + va.PTT_TAIL_S + 0.6


def test_ptt_goes_on_while_he_still_talks_after_release() -> None:
    clock = [0.0]
    stt = _STT("on fait le drake maintenant")
    a, asked, _b = _assistant(stt, clock)
    audio = _clip() + _clip()                  # released after 1 s, he keeps talking
    mic = _ClockMic(_blocks(audio), clock)
    a.key_down = lambda: clock[0] < 1.0
    a._question_ptt(mic)
    assert a.ptt_seconds >= len(audio) / 32000 and asked == ["on fait le drake maintenant"]


def test_ptt_unknown_key_state_ends_on_long_silence_and_nothing_said() -> None:
    clock = [0.0]
    stt = _STT("")
    a, asked, bips = _assistant(stt, clock)
    a.key_down = lambda: None
    a._question_ptt(_ClockMic([], clock))
    assert stt.calls == [] and bips == ["press", "end"] and a.ptt_seconds <= va.QUESTION_START_S + 2.6
    assert asked == []


def test_ptt_hotkey_state_is_none_off_windows() -> None:
    a = va.VoiceAssistant(mic_factory=lambda n: None, recognizer_factory=lambda g: None)
    assert a.key_down() in (None, False)


@pytest.mark.parametrize("heard,fixed", [
    ("Kaïssa a quoi comme objets ?", "Kai'Sa a quoi comme objets ?"),
    ("comment je joue contre Belvette ?", "comment je joue contre Bel'Veth ?"),
    ("Ksanté il est fort", "K'Santé il est fort"),
    ("je prends la lame d infini", "je prends la Lame d'infini"),
    ("Kazix il est invisible", "Kha'Zix il est invisible"),
    ("c'est quoi le power spike", "c'est quoi le power spike"),
    ("il est où le jungler ?", "il est où le jungler ?"),
    ("on fait la tour ou on rentre ?", "on fait la tour ou on rentre ?"),
])
def test_fix_terms_restores_league_names_only(heard: str, fixed: str) -> None:
    assert stt_local.fix_terms(heard, ["Zed", "Garen"]) == fixed


def test_preprocess_and_options_never_break_audio() -> None:
    x = stt_local.pcm_to_float(_clip())
    for o in ({"highpass": True}, {"norm": True}, {"gate": True}, {}):
        y = stt_local.preprocess(x, o)
        assert y.shape == x.shape and np.isfinite(y).all()
    assert stt_local.DECODE["fix_terms"] is True
    assert stt_local.fix_terms("", []) == ""
