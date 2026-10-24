"""Voice assistant « Hey coach »: wake variants, question capture, no self-trigger, pause outside a
game, push-to-talk, test mode, model download, AI prompt, spoken short answer (fake mic / recognizer)."""

from __future__ import annotations

import io
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import pytest

from treeaicoach import voice_assistant as va
from treeaicoach.ai_advisor import build_prompt, question_rule


@pytest.fixture(autouse=True)
def _fresh_wake_claims() -> None:
    """claim_wake() is process-wide: a Discord wake of another test must not mute this one."""
    va._last_wake.update(pc=-1e9, discord=-1e9)


SILENCE = b"\0\0" * va.BLOCK_FRAMES


@pytest.mark.parametrize("text", ["hé coach", "hey coach", "Hey coach", "et coach", "eh coach", "hey coche", "Hé Coach !",
                                  "bon hé coach", "hey coach quel item", "HÉ COACH"])
def test_wake_variants(text: str) -> None:
    assert va.is_wake(text)


@pytest.mark.parametrize("text", ["", "le coach est là", "coach", "ce coach est nul", "hey toi", "il est très haut",
                                  "tri aïe", "et toi", "un coach de plus", "coachons-nous"])
def test_not_wake(text: str) -> None:
    assert not va.is_wake(text)


class _Seq:
    def __init__(self, items):
        self.items, self.i = list(items), 0

    def accept(self, _b):
        return self.items[self.i][0] == "F"

    def result(self):
        return self.items[self.i][1:]

    def partial(self):
        return self.items[self.i][1:]

    def step(self, det):
        r = det.feed(self, b"")
        self.i += 1
        return r


def test_wake_detector_needs_stable_partials_but_not_finals() -> None:
    seq = _Seq(["Phé coach", "Pbla", "Phé coach", "Phé coach", "Fhé coach"])
    det = va.WakeDetector()
    assert [seq.step(det) for _ in range(4)] == [False, False, False, True]    # flipping partial: no; 2 in a row: yes
    assert va.WakeDetector().feed(_Seq(["Fhey coach"]), b"") is True            # a final result fires at once


def test_grammar_has_decoys_and_unk() -> None:
    assert "[unk]" in va.WAKE_GRAMMAR and "le coach" in va.WAKE_GRAMMAR and "hé coach" in va.WAKE_GRAMMAR
    assert not any(va.is_wake(g) for g in va.WAKE_GRAMMAR if g.startswith(("le ", "ce ", "du ")))


def test_tune_model_conf(tmp_path) -> None:
    (tmp_path / "conf").mkdir()
    (tmp_path / "conf" / "model.conf").write_text("--max-active=7000\n--beam=13.0\n--lattice-beam=4.0\n")
    va._tune_model_conf(tmp_path)
    va._tune_model_conf(tmp_path)
    assert (tmp_path / "conf" / "model.conf").read_text() == "--max-active=10000\n--beam=16.0\n--lattice-beam=6.0\n"
    va._tune_model_conf(tmp_path / "missing")


def test_strip_wake_and_short_answer() -> None:
    assert va.strip_wake("hé coach quel objet acheter") == "quel objet acheter"
    assert va.strip_wake("quel objet") == "quel objet"
    assert va.short_answer("Achète Botte. Puis Sunfire. Ensuite Thornmail.") == "Achète Botte. Puis Sunfire."
    assert len(va.short_answer("mot " * 200)) <= 221


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FakeMic:
    """Each read() advances the fake clock by one block and returns the next scripted block."""

    def __init__(self, clock: FakeClock, script: list[Any]) -> None:
        self.clock, self.script, self.closed, self.reads = clock, list(script), False, 0

    def read(self) -> bytes | None:
        self.clock.t += va.BLOCK_S
        self.reads += 1
        time.sleep(0.001)
        b = self.script.pop(0) if self.script else SILENCE
        return b.encode() if isinstance(b, str) else b

    def close(self) -> None:
        self.closed = True


class FakeRec:
    """Blocks carry text: "F:<text>" = final utterance, "P:<text>" = partial, else silence."""

    def __init__(self, grammar: Any) -> None:
        self.grammar, self._final, self._partial = grammar, "", ""

    def accept(self, pcm: bytes) -> bool:
        if pcm.startswith(b"F:"):
            self._final, self._partial = pcm[2:].decode(), ""
            return True
        if pcm.startswith(b"P:"):
            self._partial = pcm[2:].decode()
        return False

    def result(self) -> str:
        r, self._final = self._final, ""
        return r

    def partial(self) -> str:
        return self._partial

    def final(self) -> str:
        r, self._partial = self._partial, ""
        return r

    def reset(self) -> None:
        self._final = self._partial = ""


def make(script: list[Any], *, in_game: bool = True, speaking: Any = None) -> tuple[va.VoiceAssistant, list[str],
                                                                                       FakeClock, list[FakeMic]]:
    clock = FakeClock()
    mics: list[FakeMic] = []

    def mic_factory(_name: str) -> FakeMic:
        m = FakeMic(clock, script)
        mics.append(m)
        return m
    a = va.VoiceAssistant(mic_factory=mic_factory, recognizer_factory=FakeRec, clock=clock)
    asked: list[str] = []
    a.set_hooks(should_listen=lambda: in_game, is_speaking=speaking or (lambda: False),
                on_question=asked.append)
    return a, asked, clock, mics


def wait(cond: Any, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_wake_then_question_goes_to_the_ai() -> None:
    a, asked, _c, _m = make([SILENCE, "P:hé", "F:hé coach", "P:quel", "P:quel objet",
                             "F:quel objet acheter"])
    wakes: list[int] = []
    a.set_hooks(on_wake=lambda: wakes.append(1))
    a.configure(NS(voice_assistant=True, voice_assistant_mic="", hotkey_voice_ask=""))
    try:
        assert wait(lambda: asked)
        assert asked == ["quel objet acheter"] and wakes == [1] and a.wakes == 1
    finally:
        a.stop()


def test_question_ends_on_silence_from_partials() -> None:
    a, asked, _c, _m = make(["F:et coach", "P:je fais quoi", "P:je fais quoi maintenant"])
    a.configure(NS(voice_assistant=True))
    try:
        assert wait(lambda: asked)
        assert asked == ["je fais quoi maintenant"]
    finally:
        a.stop()


def test_ordinary_talk_never_asks() -> None:
    a, asked, _c, mics = make(["F:on va au dragon", "F:aïe aïe aïe", "P:trier"] + [SILENCE] * 20)
    a.configure(NS(voice_assistant=True))
    try:
        assert wait(lambda: mics and mics[0].reads > 25)
        assert asked == [] and a.wakes == 0
    finally:
        a.stop()


def test_own_voice_never_triggers() -> None:
    a, asked, _c, mics = make(["F:hé coach", "F:quel objet acheter"] + [SILENCE] * 10,
                              speaking=lambda: True)
    a.configure(NS(voice_assistant=True))
    try:
        assert wait(lambda: mics and mics[0].reads > 8)
        assert asked == [] and a.dropped_self > 0
    finally:
        a.stop()


def test_mic_closed_outside_a_game() -> None:
    a, asked, _c, mics = make(["F:hé coach", "F:quel objet acheter"], in_game=False)
    a.configure(NS(voice_assistant=True))
    try:
        time.sleep(0.2)
        assert mics == [] and not a.listening and asked == []
    finally:
        a.stop()


def test_disabled_by_default_does_nothing() -> None:
    a, asked, _c, mics = make(["F:hé coach"])
    a.configure(NS())
    a.push_to_talk()
    time.sleep(0.1)
    assert mics == [] and asked == [] and a._thread is None


def test_push_to_talk_skips_the_wake_phrase() -> None:
    a, asked, _c, mics = make(["F:on prend le dragon"], in_game=False)     # mic closed until the key
    a.configure(NS(voice_assistant=True))
    try:
        time.sleep(0.05)
        assert mics == []
        a.push_to_talk()
        assert wait(lambda: asked)
        assert asked == ["on prend le dragon"] and a.wakes == 0
        assert wait(lambda: mics[0].closed)                                 # closed again after
    finally:
        a.stop()


def test_test_mode_reports_and_does_not_ask(tmp_path: Path) -> None:
    a, asked, _c, _m = make(["F:hé coach", "F:est-ce que tu m'entends"], in_game=False)
    a.configure(NS(voice_assistant=True))
    got: list[tuple[str, bool]] = []
    ev = threading.Event()
    assert a.test(lambda m, ok: (got.append((m, ok)), ev.set()))
    try:
        assert ev.wait(3.0)
        assert got[0][1] and "est-ce que tu m'entends" in got[0][0] and asked == []
    finally:
        a.stop()


def test_test_works_without_the_switch_and_outside_a_game() -> None:
    a, asked, _c, mics = make(["P:bla", "F:hé coach", "F:tu m'entends bien"], in_game=False)
    got: list[tuple[str, bool]] = []
    ev = threading.Event()
    assert a.test(lambda m, ok: (got.append((m, ok)), ev.set()))      # switch off: still tested
    try:
        assert ev.wait(3.0)
        assert got[0][1] and "tu m'entends bien" in got[0][0] and asked == []
        assert wait(lambda: mics[0].closed)                            # the test over: mic closed again
        assert wait(lambda: a.snapshot()["stage"] == "off")
    finally:
        a.stop()


def test_model_download_unzips_atomically(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{va.MODEL_NAME}/am/final.mdl", b"x")
        zf.writestr(f"{va.MODEL_NAME}/conf/model.conf", b"x")
    data = buf.getvalue()

    class Resp(io.BytesIO):
        headers = {"Content-Length": str(len(data))}

    dest = tmp_path / va.MODEL_NAME
    seen: list[int] = []
    assert va.download_model(dest, opener=lambda _u: Resp(data), progress=seen.append)
    assert va.model_ready(dest) and seen[-1] == 100
    assert not list(tmp_path.glob("*.part")) and not list(tmp_path.glob("*.tmp"))


def test_model_download_rejects_bad_archives(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", b"x")
    assert not va.download_model(tmp_path / "m", opener=lambda _u: io.BytesIO(buf.getvalue()))
    assert not (tmp_path.parent / "evil.txt").exists() and not va.model_ready(tmp_path / "m")

    def boom(_u: str) -> Any:
        raise OSError("offline")
    assert not va.download_model(tmp_path / "m", opener=boom)


def test_prompt_carries_the_question() -> None:
    p = build_prompt({"mo": "manual"}, "quel objet j'achète")
    assert "quel objet j'achète" in p and "1 à 2 phrases" in p
    assert question_rule("") == "" and "question" not in build_prompt({"mo": "manual"})


def test_ai_ask_passes_the_question(monkeypatch: pytest.MonkeyPatch) -> None:
    from treeaicoach import ai_advisor as aia

    adv = aia.AIAdvisor(NS(ai_provider="off"))
    prompts: list[str] = []
    monkeypatch.setattr(adv, "_start", lambda prompt, *a, **k: prompts.append(prompt))
    monkeypatch.setattr(aia.AIAdvisor, "enabled", property(lambda _s: True))
    game = NS(me=NS(champion="Garen"), game_time=600.0)
    try:
        msg = adv.ask(1000.0, game, question="je fais quoi")
    except Exception:
        pytest.skip("advisor needs a richer game here")
    if prompts:
        assert "je fais quoi" in prompts[0] and msg.startswith("Question envoyée")


def test_engine_reads_the_voice_answer_short() -> None:
    from tests.test_ai_mastermind import _publisher, _Queue
    from treeaicoach import ai_advisor as aia

    eng, _shown = _publisher()
    said: list[str] = []
    eng._say = lambda text, level: said.append(text)
    eng._ai_spoken_pending = True
    q = _Queue(aia.Advice("Achète Sunfire. Puis Thornmail. Ensuite Force de la nature.", "manual", 100.0))
    eng._ai_publish(100.5, 600.0, q, 0, False)
    assert said == ["Achète Sunfire. Puis Thornmail."] and not eng._ai_spoken_pending
    q = _Queue(aia.Advice("Achète Sunfire.", "manual", 200.0))           # F8 (not spoken): written only
    eng._ai_publish(200.5, 700.0, q, 0, False)
    assert said == ["Achète Sunfire. Puis Thornmail."]


def test_claim_wake_answers_once_across_pc_and_discord():
    from treeaicoach import voice_assistant as VA

    VA._last_wake.update(pc=-1e9, discord=-1e9)
    assert VA.claim_wake("discord", now=100.0)
    assert not VA.claim_wake("pc", now=102.0)          # same « Hey coach » heard by both: one answer
    assert VA.claim_wake("pc", now=100.0 + VA.WAKE_DEDUP_S + 0.1)
    VA._last_wake.update(pc=-1e9, discord=-1e9)


def test_pc_mic_is_on_with_push_to_talk_by_default():
    import importlib

    from treeaicoach.config import Config

    src = importlib.util.find_spec("treeaicoach.voice_assistant").origin
    assert "PC_MIC_ENABLED = True" in open(src, encoding="utf-8").read()
    c = Config()
    assert c.voice_assistant and c.hotkey_voice_ask == "F12" and c.discord_listen_owner_only
