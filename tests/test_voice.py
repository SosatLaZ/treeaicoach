"""Tests of treeaicoach.voice: queue logic with a fake backend, voice selection, beep WAV, SAPI glue."""

from __future__ import annotations

import logging
import sys
import threading
import time
import types
import wave
from pathlib import Path

import pytest

from treeaicoach import voice
from treeaicoach.voice import (
    PrintBackend,
    SpeechBackend,
    VoiceEngine,
    VoiceInfo,
    make_beep_wav,
    parse_language_ids,
    pick_voice,
    rank_voices,
)


class FakeBackend(SpeechBackend):
    """Records calls; each sentence 'lasts' ``speak_s`` seconds of real time."""

    name = "fake"

    def __init__(self, speak_s: float = 0.0, beep_s: float = 0.0, fail_speak: int = 0) -> None:
        self.speak_s = speak_s
        self.beep_s = beep_s
        self.fail_speak = fail_speak
        self.lock = threading.Lock()
        self.events: list[tuple[str, object]] = []
        self.spoken: list[tuple[str, bool]] = []
        self.configs: list[tuple[str, int, int]] = []
        self.until = 0.0
        self.closed = False

    def configure(self, voice_name: str, rate: int, volume: int) -> None:
        with self.lock:
            self.configs.append((voice_name, rate, volume))

    def speak(self, text: str, purge: bool) -> None:
        with self.lock:
            if self.fail_speak > 0:
                self.fail_speak -= 1
                raise OSError("COM error")
            self.events.append(("speak", text))
            self.spoken.append((text, purge))
            self.until = time.monotonic() + self.speak_s

    def is_speaking(self) -> bool:
        with self.lock:
            return time.monotonic() < self.until

    def purge(self) -> None:
        with self.lock:
            self.events.append(("purge", None))
            self.until = 0.0

    def beep(self, volume: int) -> float:
        with self.lock:
            self.events.append(("beep", volume))
        return self.beep_s

    def voices(self) -> list[str]:
        return ["Microsoft Hortense Desktop - French"]

    def close(self) -> None:
        self.closed = True

    def texts(self) -> list[str]:
        with self.lock:
            return [t for t, _ in self.spoken]


def _wait_for(pred, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


@pytest.fixture
def make_engine():
    engines: list[VoiceEngine] = []

    def _make(backend: SpeechBackend, **kw) -> VoiceEngine:
        eng = VoiceEngine(_backend_factory=lambda: backend, **kw)
        engines.append(eng)
        eng.start()
        assert eng.wait_ready(3.0)
        return eng

    yield _make
    for eng in engines:
        eng.stop(2.0)


# -- queue behaviour --------------------------------------------------------------------

def test_messages_are_spoken_in_order(make_engine) -> None:
    fb = FakeBackend(speak_s=0.05)
    eng = make_engine(fb)
    assert eng.backend == "fake"
    for s in ("un", "deux", "trois"):
        eng.say(s, 1)
    assert eng.wait_idle(3.0)
    assert fb.texts() == ["un", "deux", "trois"]
    assert all(not purge for _, purge in fb.spoken)
    assert eng.spoken_count == 3


def test_non_danger_waits_for_current_sentence(make_engine) -> None:
    fb = FakeBackend(speak_s=0.3)
    eng = make_engine(fb)
    t0 = time.monotonic()
    eng.say("Premier.", 1)
    assert _wait_for(lambda: fb.texts() == ["Premier."])
    eng.say("Second.", 0)
    assert _wait_for(lambda: len(fb.texts()) == 2)
    assert time.monotonic() - t0 >= 0.28
    assert ("purge", None) not in fb.events


def test_danger_purges_current_and_queued(make_engine) -> None:
    fb = FakeBackend(speak_s=5.0)
    eng = make_engine(fb, beep_on_danger=True)
    eng.say("Long message.", 1)
    assert _wait_for(lambda: fb.texts() == ["Long message."])
    eng.say("En attente.", 1)
    eng.say("Gank ! Lee Sin, recule !", 2)
    assert _wait_for(lambda: len(fb.texts()) == 2)
    assert fb.spoken[-1] == ("Gank ! Lee Sin, recule !", True)
    assert "En attente." not in fb.texts()
    kinds = [k for k, _ in fb.events]
    i_beep = kinds.index("beep")
    assert kinds[i_beep - 1] == "purge"               # current sentence cut before the beep
    assert kinds.index("beep") < len(kinds) - 1       # beep before the danger sentence
    assert eng.dropped_count >= 1


def test_danger_without_beep(make_engine) -> None:
    fb = FakeBackend()
    eng = make_engine(fb, beep_on_danger=False)
    eng.say("Danger, 3 ennemis arrivent, recule !", 2)
    assert eng.wait_idle(3.0)
    assert fb.spoken == [("Danger, 3 ennemis arrivent, recule !", True)]
    assert all(k != "beep" for k, _ in fb.events)


def test_newer_danger_during_beep_wins(make_engine) -> None:
    fb = FakeBackend(beep_s=0.3)
    eng = make_engine(fb)
    eng.say("Danger un.", 2)
    assert _wait_for(lambda: any(k == "beep" for k, _ in fb.events))
    eng.say("Danger deux.", 2)
    assert _wait_for(lambda: "Danger deux." in fb.texts())
    assert "Danger un." not in fb.texts()


def test_stale_messages_are_dropped(make_engine) -> None:
    fb = FakeBackend(speak_s=0.6)
    eng = make_engine(fb, _max_age_s=0.2)
    eng.say("Premier.", 1)
    assert _wait_for(lambda: fb.texts() == ["Premier."])
    eng.say("Trop vieux.", 1)
    time.sleep(0.9)
    assert eng.wait_idle(2.0)
    assert fb.texts() == ["Premier."]
    assert eng.dropped_count >= 1


def test_stale_with_injected_clock() -> None:
    now = [100.0]
    fb = FakeBackend()
    eng = VoiceEngine(_backend_factory=lambda: fb, _clock=lambda: now[0])
    try:
        with eng._cond:                     # queue before the thread exists
            eng._queue.append(voice._Item("Vieux.", 1, 100.0, 1))
        now[0] = 103.0                      # 3 s later: > 2.5 s
        eng.say("Frais.", 1)
        assert eng.wait_ready(3.0)
        assert eng.wait_idle(3.0)
        assert fb.texts() == ["Frais."]
    finally:
        eng.stop()


def test_queue_is_bounded(make_engine) -> None:
    fb = FakeBackend(speak_s=2.0)
    eng = make_engine(fb)
    eng.say("Occupé.", 1)
    assert _wait_for(lambda: fb.texts() == ["Occupé."])
    for i in range(50):
        eng.say(f"msg {i}", 0)
    with eng._cond:
        assert len(eng._queue) <= voice.MAX_QUEUE


def test_stop_joins_and_ignores_later_messages(make_engine) -> None:
    fb = FakeBackend(speak_s=10.0)
    eng = make_engine(fb)
    eng.say("Phrase.", 1)
    assert _wait_for(lambda: fb.texts() == ["Phrase."])
    th = eng._thread
    t0 = time.monotonic()
    eng.stop(2.0)
    assert time.monotonic() - t0 < 1.5
    assert th is not None and not th.is_alive()
    assert fb.closed
    assert not eng.is_running()
    eng.say("Après l'arrêt.", 2)
    assert fb.texts() == ["Phrase."]
    eng.stop()                               # idempotent


def test_set_params_applied_live(make_engine) -> None:
    fb = FakeBackend()
    eng = make_engine(fb)
    assert _wait_for(lambda: fb.configs == [("", 2, 100)])
    eng.set_params(voice_name="Hortense", rate=40, volume=-5, beep_on_danger=False)
    assert _wait_for(lambda: fb.configs[-1] == ("Hortense", 10, 0))
    eng.set_params(rate="nope")              # garbage keeps the previous value
    eng.say("Test.", 2)
    assert eng.wait_idle(3.0)
    assert fb.configs[-1] == ("Hortense", 10, 0)
    assert all(k != "beep" for k, _ in fb.events)
    assert eng.list_voices() == ["Microsoft Hortense Desktop - French"]


def test_mute(make_engine) -> None:
    fb = FakeBackend()
    eng = make_engine(fb)
    eng.set_muted(True)
    assert eng.muted
    eng.say("Silence.", 2)
    time.sleep(0.1)
    assert fb.texts() == []
    eng.set_muted(False)
    eng.say("Parle.", 1)
    assert eng.wait_idle(3.0)
    assert fb.texts() == ["Parle."]


def test_say_never_raises_and_cleans_text(make_engine) -> None:
    fb = FakeBackend()
    eng = make_engine(fb)
    for bad in (None, "", "   ", 12, object()):
        eng.say(bad, "garbage")  # type: ignore[arg-type]
    eng.say("  Ligne\n\tunique  ", 1)
    assert eng.wait_idle(3.0)
    assert "Ligne unique" in fb.texts()


def test_failing_factory_falls_back_to_print() -> None:
    def boom() -> SpeechBackend:
        raise RuntimeError("no SAPI")

    eng = VoiceEngine(_backend_factory=boom)
    try:
        eng.say("Bonjour.", 1)
        assert eng.wait_ready(3.0)
        assert eng.backend == "print"
        assert eng.wait_idle(3.0)
    finally:
        eng.stop()


def test_speak_failures_fall_back_to_print(caplog) -> None:
    backends: list[FakeBackend] = []

    def factory() -> SpeechBackend:
        fb = FakeBackend(fail_speak=10)
        backends.append(fb)
        return fb

    eng = VoiceEngine(_backend_factory=factory)
    try:
        with caplog.at_level(logging.INFO, logger="treeaicoach.voice"):
            eng.start()
            for i in range(3):
                eng.say(f"Essai {i}.", 1)
            assert _wait_for(lambda: eng.backend == "print")
            assert eng.wait_idle(3.0)
            eng.say("Après repli.", 1)
            assert eng.wait_idle(3.0)
        assert any("Après repli." in r.getMessage() for r in caplog.records)
    finally:
        eng.stop()


# -- real default backend (print off Windows) ------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="print backend is the non-Windows default")
def test_print_backend_off_windows(caplog) -> None:
    eng = VoiceEngine()
    assert eng.backend == "print"
    assert eng.list_voices() == []
    try:
        with caplog.at_level(logging.INFO, logger="treeaicoach.voice"):
            eng.say("Attention, Lee Sin approche.", 1)
            assert eng.wait_ready(3.0)
            assert eng.wait_idle(3.0)
        assert any("Attention, Lee Sin approche." in r.getMessage() for r in caplog.records)
        assert eng.backend == "print"
    finally:
        eng.stop()
    assert not eng.is_running()


def test_print_backend_methods() -> None:
    b = PrintBackend()
    b.configure("", 2, 100)
    b.speak("x", True)
    assert b.beep(100) == 0.0
    assert not b.is_speaking()
    assert b.voices() == []
    b.close()


# -- voice selection --------------------------------------------------------------------

def test_parse_language_ids() -> None:
    assert parse_language_ids("40C") == (0x40C,)
    assert parse_language_ids("40c;409") == (0x40C, 0x409)
    assert parse_language_ids("zz; C0C") == (0xC0C,)
    assert parse_language_ids(None) == ()


def test_pick_voice_prefers_french() -> None:
    voices = [
        VoiceInfo("Microsoft David Desktop - English (United States)", (0x409,), is_default=True),
        VoiceInfo("Microsoft Paul - French (France)", (), source="onecore"),
        VoiceInfo("Microsoft Hortense Desktop - French", (0x40C,)),
        VoiceInfo("Microsoft Claude - French (Canada)", (0xC0C,)),
    ]
    assert pick_voice(voices) == 2
    assert rank_voices(voices) == [2, 3, 1]
    assert pick_voice(voices, "microsoft paul - french (france)") == 1
    assert pick_voice(voices, "David") == 0            # explicitly wanted
    assert pick_voice(voices[:1]) is None              # no French voice: keep Windows default
    assert pick_voice([VoiceInfo("Julie")]) == 0       # description keyword


# -- beep -------------------------------------------------------------------------------

def test_make_beep_wav(tmp_path: Path) -> None:
    p = tmp_path / "sounds" / "beep.wav"
    assert make_beep_wav(p, 80)
    with wave.open(str(p), "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
        dur = w.getnframes() / w.getframerate()
    assert abs(dur - voice.BEEP_DURATION_S) < 0.01
    assert not list((tmp_path / "sounds").glob("*.tmp"))


def test_danger_beep_path_generates_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(voice, "_sounds_dir", lambda: tmp_path)
    monkeypatch.setattr(voice, "_bundled_beep", lambda: None)
    p = voice.danger_beep_path(100)
    assert p is not None and p.is_file()
    mtime = p.stat().st_mtime_ns
    assert voice.danger_beep_path(98) == p              # same volume bucket, reused
    assert p.stat().st_mtime_ns == mtime
    assert voice.danger_beep_path(0) is None


# -- SAPI glue with fake pywin32 modules ------------------------------------------------

class _FakeToken:
    def __init__(self, desc: str, lang: str) -> None:
        self.desc, self.lang, self.Id = desc, lang, f"TOKEN\\{desc}"

    def GetDescription(self) -> str:
        return self.desc

    def GetAttribute(self, name: str) -> str:
        return self.lang


class _FakeTokens:
    def __init__(self, toks: list[_FakeToken]) -> None:
        self.toks = toks
        self.Count = len(toks)

    def Item(self, i: int) -> _FakeToken:
        return self.toks[i]


class _FakeSpVoice:
    def __init__(self) -> None:
        self.toks = [_FakeToken("Microsoft Zira Desktop", "409"),
                     _FakeToken("Microsoft Hortense Desktop - French", "40C")]
        self.Voice = self.toks[0]
        self.Rate = 0
        self.Volume = 100
        self.calls: list[tuple[str, int]] = []

    def GetVoices(self) -> _FakeTokens:
        return _FakeTokens(self.toks)

    def Speak(self, text: str, flags: int) -> None:
        self.calls.append((text, flags))

    def WaitUntilDone(self, ms: int) -> bool:
        return True


@pytest.fixture
def fake_pywin32(monkeypatch):
    state: dict[str, object] = {"co_init": 0, "co_uninit": 0}
    sp = _FakeSpVoice()

    def dispatch(progid: str):
        if progid == "SAPI.SpVoice":
            return sp
        raise OSError("class not registered")          # no OneCore category

    pythoncom = types.ModuleType("pythoncom")
    pythoncom.CoInitialize = lambda: state.__setitem__("co_init", state["co_init"] + 1)  # type: ignore[attr-defined]
    pythoncom.CoUninitialize = lambda: state.__setitem__("co_uninit", state["co_uninit"] + 1)  # type: ignore[attr-defined]
    pythoncom.PumpWaitingMessages = lambda: None  # type: ignore[attr-defined]
    win32com = types.ModuleType("win32com")
    client = types.ModuleType("win32com.client")
    client.Dispatch = dispatch  # type: ignore[attr-defined]
    win32com.client = client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pythoncom", pythoncom)
    monkeypatch.setitem(sys.modules, "win32com", win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", client)
    monkeypatch.setattr(voice, "danger_beep_path", lambda volume=100: None)
    return sp, state


def test_sapi_backend_with_fake_com(fake_pywin32) -> None:
    sp, state = fake_pywin32
    b = voice.SapiBackend()
    assert state["co_init"] == 1
    assert b.voices() == ["Microsoft Zira Desktop", "Microsoft Hortense Desktop - French"]
    b.configure("", 25, 70)
    assert sp.Voice is sp.toks[1]                        # French voice chosen
    assert sp.Rate == 10 and sp.Volume == 70
    b.speak("Gank !", purge=True)
    b.speak("Info.", purge=False)
    (_, f1), (_, f2) = sp.calls
    assert f1 & voice.SVSF_ASYNC and f1 & voice.SVSF_PURGE_BEFORE_SPEAK
    assert f2 & voice.SVSF_ASYNC and not f2 & voice.SVSF_PURGE_BEFORE_SPEAK
    assert not b.is_speaking()
    b.close()
    assert state["co_uninit"] == 1


def test_engine_uses_sapi_backend_class(fake_pywin32) -> None:
    sp, _ = fake_pywin32
    eng = VoiceEngine(rate=3, _backend_factory=voice.SapiBackend)
    try:
        eng.say("Attention, Lee Sin approche.", 1)
        assert eng.wait_ready(3.0)
        assert eng.wait_idle(3.0)
        assert eng.backend == "sapi"
        assert ("Attention, Lee Sin approche.", voice.SVSF_ASYNC | voice.SVSF_IS_NOT_XML) in sp.calls
        assert sp.Rate == 3
    finally:
        eng.stop()


# -- natural voices (tts_neural + NeuralBackend / OneCoreBackend) ----------------------

from treeaicoach import tts_neural  # noqa: E402


def _wav_bytes(n: int = 2400, amp: int = 8000) -> bytes:
    import array
    import io

    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(array.array("h", [amp, -amp] * (n // 2)).tobytes())
    return bio.getvalue()


class FakePlayer:
    def __init__(self) -> None:
        self.played: list[tuple[str, int]] = []
        self.stops = 0
        self.playing = False

    def play(self, path, volume=100) -> bool:
        self.played.append((Path(path).name, volume))
        self.playing = True
        return True

    def is_playing(self) -> bool:
        return self.playing

    def stop(self) -> None:
        self.stops += 1
        self.playing = False


class FakeTTS:
    def __init__(self, known: dict[str, Path]) -> None:
        self.known = known
        self.voice = "fr-FR-DeniseNeural"
        self.rate_pct = 15
        self.gets: list[tuple[str, float]] = []
        self.prefetched: list[list[str]] = []

    def set_params(self, voice: str, rate: int) -> None:
        self.rate_pct = tts_neural.rate_percent(rate)

    def get(self, text: str, timeout: float = 1.5):
        self.gets.append((text, timeout))
        return self.known.get(text)

    def prefetch(self, texts) -> None:
        self.prefetched.append(list(texts))


def test_neural_backend_plays_cache_and_falls_back(tmp_path: Path) -> None:
    wav = tmp_path / "a.wav"
    wav.write_bytes(_wav_bytes())
    tts, player, local = FakeTTS({"Gank ! Lee Sin, recule !": wav}), FakePlayer(), FakeBackend()
    b = voice.NeuralBackend(_tts=tts, _player=player, _local_factory=lambda: local)
    b.configure("", 2, 80)
    assert tts.prefetched and "Dragon dans une minute." in tts.prefetched[-1]
    b.speak("Gank ! Lee Sin, recule !", True)
    assert player.played == [("a.wav", 80)] and player.stops >= 1
    assert tts.gets[-1][1] < tts_neural.LIVE_TIMEOUT_S        # DANGER: shorter wait
    assert b.is_speaking()
    b.speak("Phrase inconnue.", False)                       # miss -> local voice
    assert local.texts() == ["Phrase inconnue."] and local.configs == [("", 2, 80)]
    b.set_roster(["Lee Sin"], ["Ahri"])
    assert "Gank ! Lee Sin, recule !" in tts.prefetched[-1]
    b.purge()
    assert not b.is_speaking() and ("purge", None) in local.events
    b.close()
    assert local.closed


def test_engine_prefetch_and_engine_switch(monkeypatch) -> None:
    made: list[tuple[str, str]] = []
    backends: list[FakeBackend] = []

    def fake_make(engine="auto", neural_voice="", neural_rate=""):
        made.append((engine, neural_voice))
        fb = FakeBackend()
        fb.rosters = []
        fb.set_roster = lambda e, a=(): fb.rosters.append((tuple(e), tuple(a)))
        backends.append(fb)
        return fb

    monkeypatch.setattr(voice, "make_backend", fake_make)
    eng = VoiceEngine(engine="neural", neural_voice="fr-FR-HenriNeural")
    try:
        eng.start()
        assert eng.wait_ready(3.0)
        assert made == [("neural", "fr-FR-HenriNeural")]
        eng.prefetch(["Lee Sin", "Ahri", ""], ["Jinx"])
        assert _wait_for(lambda: backends[0].rosters[-1:] == [(("Lee Sin", "Ahri"), ("Jinx",))])
        eng.set_params(engine="sapi")
        assert _wait_for(lambda: len(made) == 2 and made[1][0] == "sapi")
        assert backends[0].closed
        assert _wait_for(lambda: backends[1].rosters[-1:] == [(("Lee Sin", "Ahri"), ("Jinx",))])
        eng.set_params(engine="bogus")                  # invalid -> auto
        assert _wait_for(lambda: len(made) == 3 and made[2][0] == "auto")
    finally:
        eng.stop()


def test_make_backend_off_windows_is_print() -> None:
    if sys.platform == "win32":
        pytest.skip("Windows")
    assert isinstance(voice.make_backend("neural"), PrintBackend)
    assert VoiceEngine(engine="onecore").backend == "print"


def test_onecore_backend_with_fake_synth(tmp_path: Path) -> None:
    class Synth:
        voice_name = "Microsoft Denise"

        def __init__(self) -> None:
            self.calls = 0
            self.cfg = None

        def voices(self):
            return ["Microsoft Denise"]

        def select(self, wanted):
            pass

        def configure(self, rate, volume):
            self.cfg = (rate, volume)

        def wav(self, text):
            self.calls += 1
            return _wav_bytes()

    synth, player = Synth(), FakePlayer()
    b = voice.OneCoreBackend(_synth=synth, _player=player, cache_root=tmp_path)
    b.configure("", 4, 50)
    assert synth.cfg == (4, 100)
    b.speak("Dragon dans une minute.", False)
    b.speak("Dragon dans une minute.", True)               # cached the 2nd time
    assert synth.calls == 1 and len(player.played) == 2 and player.played[0][1] == 50
    assert b.voices() == ["Microsoft Denise"]


def test_neural_tts_cache_timeout_and_offline(tmp_path: Path) -> None:
    calls: list[str] = []
    slow = threading.Event()

    def synth(text, v, rate):
        calls.append(text)
        if text == "lent":
            slow.wait(2.0)
        if text == "panne":
            raise OSError("no network")
        return _wav_bytes()

    t = tts_neural.NeuralTTS("fr-FR-HenriNeural", 2, cache_root=tmp_path, _synth=synth)
    assert t.rate_pct == 15
    p = t.get("Gank ! Lee Sin, recule !")
    assert p is not None and p.parent.name == "fr-FR-HenriNeural" and p.read_bytes()[:4] == b"RIFF"
    assert t.get("Gank ! Lee Sin, recule !") == p and calls.count("Gank ! Lee Sin, recule !") == 1
    assert t.get("lent", timeout=0.05) is None                # too slow -> None, keeps running
    slow.set()
    assert _wait_for(lambda: t.cached("lent") is not None)
    assert t.get("panne") is None and not t.online()
    assert t.get("autre") is None and "autre" not in calls      # offline: no new attempt
    t.set_params("garbage", 0)
    assert t.voice == tts_neural.DEFAULT_NEURAL_VOICE and t.rate_pct == 0


def test_neural_tts_prefetch(tmp_path: Path) -> None:
    t = tts_neural.NeuralTTS(cache_root=tmp_path, _synth=lambda text, v, r: _wav_bytes())
    texts = tts_neural.roster_phrases(["Lee Sin", "Ahri"]) + tts_neural.static_phrases()
    assert texts[0] == "Gank ! Lee Sin, recule !"
    assert "Lee Sin arrive par la rivière !" in texts and "Gank bot : Lee Sin et Ahri, recule !" in texts
    t.prefetch(texts + texts)
    assert _wait_for(lambda: all(t.cached(x) for x in texts), 10.0)


def test_mp3_decode_and_wav_player(tmp_path: Path) -> None:
    assert tts_neural.mp3_to_wav(b"") is None
    assert tts_neural.mp3_to_wav(b"not an mp3" * 50) is None
    src = tmp_path / "s.wav"
    src.write_bytes(_wav_bytes(24000))
    now = [0.0]
    played: list = []
    p = tts_neural.WavPlayer(_play=played.append, _clock=lambda: now[0])
    assert p.play(src, 50)
    assert played[-1].endswith("s_v50.wav") and p.is_playing()
    with wave.open(played[-1]) as w:
        import array
        a = array.array("h", w.readframes(4))
        assert abs(a[0]) == 4000
    now[0] = 2.0
    assert not p.is_playing()
    assert p.play(src, 100) and played[-1] == str(src)
    p.stop()
    assert played[-1] is None and not p.is_playing()


def test_config_voice_engine_fields() -> None:
    from treeaicoach.config import Config, _validate_field

    c = Config()
    assert c.voice_engine == "auto" and c.neural_voice == "fr-FR-DeniseNeural"
    assert _validate_field("voice_engine", "Neural", "auto") == "neural"
    assert _validate_field("voice_engine", "x", "auto") == "auto"
    assert _validate_field("neural_voice", "fr-FR-HenriNeural", "d") == "fr-FR-HenriNeural"
    assert _validate_field("neural_voice", "rm -rf", "d") == "d"


def test_neural_rate_prewarm_and_lists(monkeypatch) -> None:
    rates: list[str] = []
    warmed: list[tuple[str, ...]] = []

    def fake_make(engine="auto", neural_voice="", neural_rate=""):
        fb = FakeBackend()
        fb.set_neural_rate = rates.append
        fb.prewarm = lambda phrases: warmed.append(tuple(phrases))
        return fb

    monkeypatch.setattr(voice, "make_backend", fake_make)
    eng = VoiceEngine(engine="neural", neural_rate="+20 %")
    try:
        eng.start()
        assert eng.wait_ready(3.0)
        assert _wait_for(lambda: rates[-1:] == ["+20%"])
        eng.set_params(neural_rate="+500%")             # clamped
        assert _wait_for(lambda: rates[-1:] == ["+100%"])
        eng.set_params(neural_rate="vite")              # garbage -> unchanged
        eng.prewarm(["Gank ! Ahri, recule !", "", "Gank ! Ahri, recule !", None, "Baron dans 20 secondes."])
        assert _wait_for(lambda: warmed[-1:] == [("Gank ! Ahri, recule !", "Baron dans 20 secondes.")])
        assert eng._params.neural_rate == "+100%"
    finally:
        eng.stop()
    engines = VoiceEngine.list_engines()
    assert [k for k, _ in engines] == ["auto", "neural", "onecore", "sapi"] and all(lbl for _, lbl in engines)
    voices = VoiceEngine.list_neural_voices()
    assert voices[0][0] == "fr-FR-DeniseNeural" and len(voices) >= 4


def test_neural_backend_rate_and_prewarm(tmp_path: Path) -> None:
    tts, player, local = FakeTTS({}), FakePlayer(), FakeBackend()
    b = voice.NeuralBackend(neural_rate="+25%", _tts=tts, _player=player, _local_factory=lambda: local)
    b.configure("", -3, 100)                     # SAPI rate does not change the neural rate
    assert tts.rate_pct == 25
    n = len(tts.prefetched)
    b.set_neural_rate("+10%")
    assert tts.rate_pct == 10 and len(tts.prefetched) == n + 1
    b.prewarm(["Phrase A.", "Phrase B."])
    assert tts.prefetched[-1][:2] == ["Phrase A.", "Phrase B."]
    assert "Dragon dans une minute." in tts.prefetched[-1]


def test_config_neural_rate_field() -> None:
    from treeaicoach.config import Config, _validate_field

    assert Config().neural_rate == "+15%"
    assert _validate_field("neural_rate", "+20%", "+15%") == "+20%"
    assert _validate_field("neural_rate", "-10", "+15%") == "-10%"
    assert _validate_field("neural_rate", 30, "+15%") == "+30%"
    assert _validate_field("neural_rate", "+900%", "+15%") == "+100%"
    assert _validate_field("neural_rate", "rapide", "+15%") == "+15%"
    assert _validate_field("neural_rate", True, "+15%") == "+15%"
