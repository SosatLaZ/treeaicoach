"""3.4.1 « 2 minutes between the question and the answer »: the Whisper queue under friends' chatter, the
latency log of one question, « hé coque » -> « hé coach »."""

from __future__ import annotations

import logging
from types import SimpleNamespace as NS

import pytest

from treeaicoach import stt_local, voice_latency

#: realistic per-stage costs on a mid 4-core CPU while LoL runs (stt_local.LATENCY_NOTE measured idle,
#: x2 for the game's load): small beam 1 on a wake clip, base beam 1 on a 1.5 s clip, small beam 3 on a 3 s
#: question; Groq gpt-oss (reasoning low); first sentence of neural TTS; Silero's end of speech
COST = {"wake_small": 1.0, "wake_base": 0.3, "question": 1.4}
AI_S, TTS_FIRST_S, TTS_ALL_S = 0.9, 0.7, 1.3


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _CostSTT:
    """Fake Whisper: each transcription advances the simulated clock by its cost."""

    def __init__(self, clock: _Clock, wake_cost: float) -> None:
        self.clock, self.wake_cost, self.calls = clock, wake_cost, []

    def transcribe(self, pcm: bytes, prompt: str | None = None, beam: int | None = None) -> str:
        self.calls.append(pcm)
        self.clock.t += COST["question"] if pcm == b"question" else self.wake_cost
        return "hé coach quel objet" if pcm == b"question" else "on push mid"


def _simulate(new: bool, ask_at: float, chatter_every: float = 0.8, until: float = 400.0) -> stt_local.Job:
    """Friends' chatter makes a « Hey coach » candidate every ``chatter_every`` s (3 friends talking all
    the time: one each 2.4 s), each re-read by Whisper; the player asks at ``ask_at``. Old: one FIFO, the
    small model confirms, items kept long; new: the question first, base confirms, 1.5 s / 3 s limits."""
    clock = _Clock()
    stt = _CostSTT(clock, COST["wake_base"] if new else COST["wake_small"])
    w = stt_local.WhisperWorker(stt, clock=clock, autostart=False)
    if not new:
        w.MAX_WAITING = 10 ** 6                    # the confirmations waited on the decode lock: no bound
    arrivals = [(i * chatter_every, "wake") for i in range(int(until / chatter_every))] + [(ask_at, "question")]
    arrivals.sort()
    question = None
    i = 0
    while clock.t < until:
        while i < len(arrivals) and arrivals[i][0] <= clock.t:
            at, kind = arrivals[i]
            i += 1
            if kind == "question":
                job = w.submit(b"question", key="pc", prio=stt_local.PRIO_QUESTION,
                               max_age=stt_local.QUEUE_MAX_AGE_S if new else 20.0)
                question = job
            else:
                job = w.submit(b"wake", prio=stt_local.PRIO_WAKE if new else stt_local.PRIO_QUESTION,
                               max_age=stt_local.WAKE_MAX_AGE_S if new else 1e9, beam=1)
            job.t = at
        if question is not None and question.ev.is_set():
            return question
        if not w.step():
            if i >= len(arrivals):
                break
            clock.t = arrivals[i][0]
    assert question is not None
    return question


def test_backlog_reproduced_then_fixed() -> None:
    # BEFORE: the confirmations of 3 chatting friends need 1.25 s of Whisper per second: the backlog grows
    # without end; a question asked after 2 minutes of chatter waits ~30 s, and is dropped (Vosk's text)
    old = _simulate(new=False, ask_at=120.0)
    assert old.dropped or old.wait_ms > 20000
    old_early = _simulate(new=False, ask_at=10.0)
    assert old_early.wait_ms > 2000
    # AFTER: base confirms in 0.3 s (load 0.4), stale confirmations dropped, the question goes first
    new = _simulate(new=True, ask_at=120.0)
    assert not new.dropped and new.text and new.wait_ms <= COST["wake_base"] * 1000 + 1
    assert new.compute_ms == pytest.approx(COST["question"] * 1000)


def test_question_preempts_and_stale_confirmations_dropped() -> None:
    clock = _Clock()
    stt = _CostSTT(clock, 0.3)
    w = stt_local.WhisperWorker(stt, clock=clock, autostart=False)
    wakes = [w.submit(b"wake", prio=stt_local.PRIO_WAKE, max_age=1.5) for _ in range(3)]
    q = w.submit(b"question", prio=stt_local.PRIO_QUESTION)
    assert w.step() and stt.calls == [b"question"] and q.text      # first, though submitted last
    clock.t = 2.0                                                   # the confirmations waited 2 s > 1.5 s
    while w.step():
        pass
    assert all(j.dropped and j.text is None for j in wakes) and stt.calls == [b"question"]
    late = w.submit(b"question", key="pc")
    clock.t += stt_local.QUEUE_MAX_AGE_S + 0.1                      # a question older than 3 s: dropped
    assert w.step() and late.dropped


def test_latency_line_for_one_question(caplog: pytest.LogCaptureFixture) -> None:
    clk = _Clock()
    voice_latency.reset()
    old_clock, voice_latency.clock = voice_latency.clock, clk
    try:
        clk.t = 100.0
        voice_latency.wake(300)
        clk.t = 102.0
        voice_latency.begin("discord", vad_ms=700)                  # Silero: 0.7 s of silence after the words
        voice_latency.add("file", 0)
        voice_latency.add("whisper", 1400)
        clk.t = 103.4
        voice_latency.asked()
        clk.t = 103.4 + AI_S
        voice_latency.answered("Achète Sunfire. Puis Thornmail.")
        assert voice_latency.playing("Autre phrase du coach") is None   # not the answer: still open
        clk.t += TTS_FIRST_S
        with caplog.at_level(logging.INFO, logger="treeaicoach.voice_latency"):
            rec = voice_latency.playing("Achète Sunfire.")
        assert rec is not None and rec["vad"] == 700 and rec["ia"] == 900 and rec["voix"] == 700
        assert rec["total"] == 700 + 1400 + 900 + 700 and rec["wake"] == 300
        assert "Latence question : vad 700 ms · file 0 · whisper 1400 · ia 900 · voix 700 · total 3700 ms" \
            in caplog.text
        snap = voice_latency.snapshot()
        assert snap["median"]["total"] == 3700 and snap["last"].startswith("vad 700")
    finally:
        voice_latency.clock = old_clock
        voice_latency.reset()


def test_chain_before_after_budget() -> None:
    """The whole chain, worst case of each stage (the limits that add up), before -> after."""
    before = {"vad": 1.0 + 0.25, "file": 20.0, "whisper": 1.4, "ia_hold": 60.0, "ia": AI_S,
              "voix": TTS_ALL_S + 40.0}                      # Discord answer kept 40 s behind other calls
    after = {"vad": 0.7 + 0.25, "file": COST["wake_base"], "whisper": COST["question"], "ia_hold": 0.0,
             "ia": AI_S, "voix": TTS_FIRST_S}
    assert sum(before.values()) > 120.0                      # « about 2 minutes »
    assert sum(after.values()) < 5.0
    assert stt_local.QUEUE_MAX_AGE_S == 3.0 and stt_local.WAKE_MAX_AGE_S <= 1.5


def test_wake_confirmation_clip_short() -> None:
    from treeaicoach import voice_assistant as va

    seen: list[int] = []
    stt = NS(transcribe=lambda pcm, prompt="", beam=None: (seen.append(len(pcm)), "hé coach")[1])
    assert va.whisper_confirm(stt, b"\0\0" * 16000 * 4) is True
    assert seen == [int(va.CONFIRM_AUDIO_S * 16000) * 2] and va.CONFIRM_AUDIO_S <= 1.5


@pytest.mark.parametrize("text,want", [
    ("Hé coque, quel objet ?", "Hé coach, quel objet ?"),
    ("coq quel objet", "coach quel objet"),
    ("Et coke je fais quoi", "Et coach je fais quoi"),
    ("Coque quel build", "Coach quel build"),
    ("une coque de noix", "une coque de noix"),
])
def test_coque_becomes_coach_at_the_start(text: str, want: str) -> None:
    from treeaicoach import voice_assistant as va

    assert stt_local.fix_terms(text) == want
    if "noix" not in text:
        assert va.whisper_wake(text)[0]                     # Whisper's « hé coque » wakes…
    assert va.wake_kind("hé coq le [unk]", [0.9, 0.9, 0.9, 1.0], final=True) == ""   # …Vosk's « coq » alone not
