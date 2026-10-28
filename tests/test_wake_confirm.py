"""« Hey coach » must be CONFIRMED (3.1 field report: the bot woke up by itself, « mot-clé entendu (bare,
grammaire) dans « printemps » », « … dans « hum tu » », « (strong, grammaire) dans « un coach » » then
« question de 0 mot(s) »): the grammar recognizer alone is never trusted, the free recognizer's own words or
Whisper must agree; « Coach, … » alone needs a silence before it. Fakes only (tools/wake_bench.py measures
the real models)."""
from __future__ import annotations

import numpy as np
import pytest

from treeaicoach import discord_listen as dl
from treeaicoach import voice_assistant as va


@pytest.fixture(autouse=True)
def _fresh_wake_claims() -> None:
    va._last_wake.update(pc=-1e9, discord=-1e9)


BLOCK = va.BLOCK_FRAMES
LOUD = np.full(BLOCK, 3000, "<i2").tobytes()
QUIET = bytes(BLOCK * 2)


class Rec:
    """One scripted output per block: ("F"|"P", text, confidences)."""

    def __init__(self, items=None) -> None:  # noqa: ANN001
        self.items, self.i = list(items or []), -1
        self.last_raw, self.last_confs = "", None

    def accept(self, pcm: bytes) -> bool:
        self.i += 1
        k, t, c = self.items[self.i] if self.i < len(self.items) else ("P", "", None)
        self.last_raw, self.last_confs = t, c
        return k == "F"

    def result(self) -> str:
        return self.last_raw.replace("[unk]", "").strip()

    def partial(self) -> str:
        return self.result()

    def final(self) -> str:
        return ""

    def reset(self) -> None:
        pass


def engine(gram: list, free: list | None, confirm=None) -> va.WakeEngine:  # noqa: ANN001
    """``gram``: one item per block; ``free``: one item per SPEECH block (it only runs while someone talks)."""
    g, f = Rec(gram), (Rec(free) if free is not None else None)
    eng = va.WakeEngine(lambda grammar: g if grammar else f)
    eng.confirm = confirm
    return eng


def run(eng: va.WakeEngine, blocks: list[bytes]) -> list[bool]:
    return [eng.feed(b) for b in blocks]


GRAMMAR_WAKE = [("P", "", None), ("P", "", None), ("F", "hé coach", [0.95, 0.95])]


# ------------------------------------------------------------------------------ the logged false wakes
@pytest.mark.parametrize("heard", ["printemps", "hum tu", "un coach", "mon coach dit qu'il faut farmer"])
def test_grammar_alone_never_wakes(heard: str) -> None:
    """The free recognizer wrote something else for the same audio: dropped (no chime, no question)."""
    eng = engine(GRAMMAR_WAKE, [("P", "", None), ("P", "", None), ("F", heard, [0.9] * len(heard.split()))])
    assert not any(run(eng, [QUIET, QUIET, LOUD] + [QUIET] * 8))
    assert eng.dropped == 1 and eng.last_dropped


@pytest.mark.parametrize("heard", ["printemps", "hum tu"])
def test_grammar_bare_coach_on_noise_is_dropped(heard: str) -> None:
    eng = engine([("P", "", None), ("P", "", None), ("P", "coach", None), ("P", "coach", None)],
                 [("P", "", None), ("P", "", None), ("P", heard, None), ("P", heard, None)])
    assert not any(run(eng, [QUIET, QUIET, LOUD, LOUD] + [QUIET] * 8))


def test_whisper_says_no_drops_the_candidate_and_yes_confirms_it() -> None:
    eng = engine(GRAMMAR_WAKE, [("P", "", None)] * 3, confirm=lambda pcm: False)
    assert not any(run(eng, [QUIET, QUIET, LOUD] + [QUIET] * 8))
    seen: list[int] = []
    eng = engine(GRAMMAR_WAKE, [("P", "", None)] * 3, confirm=lambda pcm: seen.append(len(pcm)) or True)
    got = run(eng, [QUIET, QUIET, LOUD] + [QUIET] * 8)
    assert sum(got) == 1 and eng.last_source == "whisper"
    assert 0 < seen[0] <= int(va.CONFIRM_AUDIO_S * va.SAMPLE_RATE) * 2      # only the last ~2 s


def test_free_recognizer_confirms_the_grammar_wake() -> None:
    eng = engine(GRAMMAR_WAKE, [("P", "", None), ("P", "", None), ("P", "et coach", None), ("P", "et coach", None)])
    got = run(eng, [QUIET, QUIET, LOUD, LOUD, QUIET])
    assert sum(got) == 1 and eng.confirmed_by == "libre"


def test_bare_coach_needs_whisper_when_loaded() -> None:
    free = [("P", "coach on fait", None), ("P", "coach on fait le drake", None)]
    eng = engine([("P", "", None)] * 4, free, confirm=lambda pcm: False)
    assert not any(run(eng, [QUIET, QUIET, LOUD, LOUD]))
    eng = engine([("P", "", None)] * 4, free, confirm=lambda pcm: True)
    assert any(run(eng, [QUIET, QUIET, LOUD, LOUD]))


def test_bare_coach_talked_about_is_not_a_wake_without_whisper() -> None:
    free = [("P", "coach il est", None), ("P", "coach il est nul", None)]
    eng = engine([("P", "", None)] * 4, free)
    assert not any(run(eng, [QUIET, QUIET, LOUD, LOUD]))
    free = [("P", "coach on", None), ("P", "coach on fait quoi", None)]
    eng = engine([("P", "", None)] * 4, free)
    assert any(run(eng, [QUIET, QUIET, LOUD, LOUD]))


def test_without_a_free_recognizer_whisper_decides() -> None:
    eng = engine([("F", "hé coach", [1, 1])], None, confirm=lambda pcm: False)
    assert eng.feed(LOUD) is False
    eng = engine([("F", "hé coach", [1, 1])], None, confirm=lambda pcm: True)
    assert eng.feed(LOUD) is True and eng.last_source == "whisper"


@pytest.mark.parametrize("text,ok", [
    ("Hé coach, quel objet je fais ?", True), ("Hey coach.", True), ("Et Coatch, il est où ?", True),
    ("Coach, on fait le drake ?", True), ("Hey Tree, le baron ?", True), ("bon, hé coach, je back ?", True),
    ("Printemps.", False), ("Hum, tu crois ?", False), ("Un coach, ça sert à rien.", False),
    ("Mon coach dit qu'il faut farmer.", False), ("Coach est nul.", False), ("Et le coach, il dit quoi ?", False),
    ("Hé, quelqu'un a une ward ?", False), ("", False),
])
def test_whisper_confirms(text: str, ok: bool) -> None:
    assert va.whisper_confirms(text) is ok


@pytest.mark.parametrize("text,ok", [("et coach", True), ("hé coche", True), ("hey tri", True), ("un coach", False),
                                     ("printemps", False), ("hum tu", False), ("le coach", False), ("", False)])
def test_free_confirms(text: str, ok: bool) -> None:
    assert va.free_confirms(text) is ok


def test_whisper_confirm_uses_no_prompt_and_beam_one() -> None:
    calls = []

    class W:
        def transcribe(self, pcm, prompt=None, beam=None):  # noqa: ANN001, ANN202
            calls.append((prompt, beam))
            return "Hé coach !"
    assert va.whisper_confirm(W(), b"\0\0" * 100) is True and calls == [("", 1)]
    assert va.whisper_confirm(None, b"\0\0") is None


# ------------------------------------------------------------------------------ Discord: no chime, no question
WAKE = 1000


class DRec:
    def __init__(self, grammar) -> None:  # noqa: ANN001
        self.g, self._r = grammar is not None, ""

    def accept(self, pcm: bytes) -> bool:
        v = int(np.frombuffer(pcm, dtype="<i2")[0])
        if v != WAKE:
            return False
        self._r = "hé coach" if self.g else "un coach"       # the grammar forces it, the free one disagrees
        return True

    def result(self) -> str:
        r, self._r = self._r, ""
        return r

    def partial(self) -> str:
        return ""

    def final(self) -> str:
        return ""

    def reset(self) -> None:
        pass


def test_discord_unconfirmed_wake_no_chime_no_question() -> None:
    clock = [100.0]
    lst = dl.VoiceListener(make_rec=DRec, clock=lambda: clock[0], threaded=False)
    lst.should_listen = lambda: True
    chimes: list[str] = []
    asked: list[tuple[str, str]] = []
    lst.on_wake = chimes.append
    lst.on_question = lambda u, q: asked.append((u, q))
    for v in [0, 0, WAKE] + [0] * 30:
        clock[0] += 0.25
        lst.step(("7", np.full(BLOCK, v, "<i2").tobytes()))
    assert chimes == [] and asked == [] and lst.wakes == 0
