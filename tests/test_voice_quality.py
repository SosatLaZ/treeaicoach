"""Voice quality: French pronunciation lexicon, soft chimes, natural-voice-first fallbacks."""

from __future__ import annotations

import os
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from treeaicoach import chimes, tts_neural, voice
from treeaicoach.tts_lexicon import CHAMPIONS, speakable
from treeaicoach.voice import VoiceEngine, VoiceInfo

ROOT = Path(__file__).resolve().parents[1]


# -- pronunciation ----------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Gank ! Kai'Sa, recule !", "Gank ! Kaïssa, recule !"),
    ("GANK ! Lee Sin arrive", "Gank ! Li Sine arrive"),
    ("Bel'Veth et K'Santé : 1v2, recule !", "Bèl Vèth et Késanté : un contre deux, recule !"),
    ("Nunu & Willump arrive", "Nounou et Ouiloump arrive"),
    ("Nunu et Willump arrive", "Nounou et Ouiloump arrive"),
    ("Baron à 20:00, drake à 8:30", "Baron à 20 minutes, drèk à 8 minutes 30"),
    ("Ton ult est prêt, 40% de PV", "Ton ulti est prêt, 40 % de P.V."),
    ("Le jungler est bot", "Le jungleur est botte"),
    ("Dr. Mundo et Jarvan IV", "Docteur Moundo et Jarvan quatre"),
    ("Cho’Gath arrive", "Cho Gath arrive"),
    ("7 CS/min", "7 C.S. par minute"),
    ("Yunara, Swain et Garen", "Younara, Souène et Garène"),
])
def test_speakable(text: str, expected: str) -> None:
    assert speakable(text) == expected


def test_speakable_plain_french_unchanged_and_robust() -> None:
    for t in ("Ils sont trois morts : Baron !", "Recule !", "Dragon dans une minute.",
              "Pense à acheter une balise de contrôle."):
        assert speakable(t) == t
    assert speakable("") == "" and speakable(None) == ""      # type: ignore[arg-type]
    assert speakable("Garenne") == "Garenne"                    # whole names only


def test_speakable_idempotent_on_every_prefetched_line() -> None:
    names = list(CHAMPIONS)[:40]

    class P:
        def __init__(self, n: str) -> None:
            self.champion_name = n

    class G:
        enemies = [P(n) for n in names[:5]]
        allies = [P(n) for n in names[5:10]]

    lines = tts_neural.build_phrase_list(G())
    assert len(lines) > 300
    for line in lines:
        once = speakable(line)
        assert speakable(once) == once, line


def test_neural_cache_key_uses_pronunciation(tmp_path: Path) -> None:
    calls: list[str] = []
    t = tts_neural.NeuralTTS(cache_root=tmp_path, _synth=lambda text, v, r: calls.append(text) or _wav())
    assert t.get("Kai'Sa arrive !", 2.0) is not None
    assert calls == ["Kaïssa arrive !"]
    assert t.cached("Kai'Sa arrive !") == t.cached("Kaïssa arrive !") is not None


def _wav(n: int = 4000) -> bytes:
    import io

    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes((np.sin(np.arange(n) / 5.0) * 8000).astype("<i2").tobytes())
    return bio.getvalue()


# -- chimes -----------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(chimes.SOUNDS))
def test_chime_is_smooth(name: str) -> None:
    x = chimes.render(name)
    sr = chimes.SAMPLE_RATE
    a = np.abs(x)
    first = int(np.argmax(a > 10 ** (-40 / 20)))
    assert first / sr < 0.008                                   # audible at once (beep-first)
    assert a[-1] < 1e-4 and a[0] == 0.0                         # starts and ends at silence: no click
    assert abs(float(np.mean(x))) < 0.01                        # no DC offset
    peak_db = 20 * np.log10(a.max())
    assert abs(peak_db - chimes.SOUNDS[name].peak_db) < 0.2     # normalised level
    assert len(x) / sr < 0.65
    # attack: no jump from silence to a loud sample (raised-cosine attack: < 50 % of the peak at 0.5 ms)
    assert a[first: first + int(0.0005 * sr)].max() < 0.5 * a.max()


def test_danger_louder_than_info_and_short() -> None:
    for d in chimes.DANGER_TONES:
        assert chimes.SOUNDS[d].peak_db > chimes.SOUNDS["warning"].peak_db > chimes.SOUNDS["info"].peak_db
    assert chimes.duration_s("gank") < 0.36 and chimes.duration_s("recule") < 0.38


def test_bundled_sound_assets_match_generator() -> None:
    d = ROOT / "treeaicoach" / "assets" / "sounds"
    total = 0
    for name in chimes.SOUNDS:
        p = d / f"{name}.wav"
        assert p.is_file(), f"run python -m tools.make_sounds ({name})"
        total += p.stat().st_size
        with wave.open(str(p), "rb") as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, chimes.SAMPLE_RATE)
            got = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32767.0
        ref = chimes.render(name)
        assert len(got) == len(ref) and np.max(np.abs(got - ref)) < 2e-3
    assert total < 600 * 1024


def test_wav_bytes_volume_and_unknown() -> None:
    loud, quiet = chimes.wav_bytes("gank", 100), chimes.wav_bytes("gank", 30)
    assert loud[:4] == b"RIFF" and len(loud) == len(quiet)
    assert chimes.wav_bytes("nope") == b""


def test_beep_player_uses_chimes() -> None:
    got: list[bytes] = []
    ev = threading.Event()
    bp = voice.BeepPlayer(_play=lambda d: (got.append(d), ev.set()))
    assert bp.play("recule", 100)
    assert ev.wait(2.0)
    assert got[0] == chimes.wav_bytes("recule", 100)
    assert abs(voice.tone_duration_s("gank") - chimes.duration_s("gank")) < 1e-9
    assert bp.play("objective", 100)


# -- voice selection / fallbacks --------------------------------------------------------

def test_onecore_and_natural_voices_preferred() -> None:
    vs = [VoiceInfo("Microsoft Hortense Desktop - French", (0x40C,), "sapi5"),
          VoiceInfo("Microsoft Paul - French (France)", (0x40C,), "onecore"),
          VoiceInfo("Microsoft Julie - French (France)", (0x40C,), "onecore"),
          VoiceInfo("Microsoft Denise Online (Natural) - French (France)", (0x40C,), "onecore")]
    order = voice.rank_voices(vs)
    assert [vs[i].description.split()[1] for i in order] == ["Denise", "Julie", "Paul", "Hortense"]


def test_neural_misses_go_to_onecore_not_sapi(monkeypatch) -> None:
    asked: list[str] = []
    monkeypatch.setattr(voice, "_local_backend", lambda engine="onecore": asked.append(engine) or voice.PrintBackend())

    class T:
        voice, rate_pct = "fr-FR-DeniseNeural", 15

        def set_params(self, *a): pass

        def get(self, text, timeout=1.5, abort=None): return None

        def prefetch(self, texts): pass

        def cached(self, text): return None

    b = voice.NeuralBackend(_tts=T(), _player=_Player())
    b.speak("Phrase pas prête.", False)
    assert asked == ["onecore"] and b.last_source == "print"


class _Player:
    def play(self, path, volume=100): return True

    def is_playing(self): return False

    def stop(self): pass


class _NeuralFake(voice.SpeechBackend):
    """A neural-like backend: records warm() and the speak timeout."""

    name = "neural"

    def __init__(self) -> None:
        self.warmed: list[str] = []
        self.calls: list[tuple[str, str, float | None]] = []
        self.last_source = "neural"

    def warm(self, text: str) -> None:
        self.warmed.append(text)

    def speak(self, text: str, purge: bool, timeout: float | None = None) -> None:
        self.calls.append(("speak", text, timeout))

    def speak_urgent(self, text: str, purge: bool) -> None:
        self.calls.append(("urgent", text, None))


def _engine(backend, beeper=None) -> VoiceEngine:
    eng = VoiceEngine(_backend_factory=lambda: backend, _beep_player=beeper)
    eng.start()
    assert eng.wait_ready(3.0)
    return eng


def test_info_lines_wait_for_natural_voice_and_are_warmed() -> None:
    b = _NeuralFake()
    eng = _engine(b)
    try:
        eng.say("Conseil de l'IA : pousse la vague.", 0)
        eng.say("Lee Sin arrive !", 1)
        assert eng.wait_idle(3.0)
        assert "Conseil de l'IA : pousse la vague." in b.warmed and "Lee Sin arrive !" in b.warmed
        kinds = {t: (k, to) for k, t, to in b.calls}
        assert kinds["Lee Sin arrive !"][0] == "urgent"
        assert kinds["Conseil de l'IA : pousse la vague."] == ("speak", tts_neural.PATIENT_TIMEOUT_S)
        eng.preview()
        assert eng.wait_idle(3.0)
        assert b.calls[-1] == ("speak", voice.PREVIEW_TEXT, voice.PREVIEW_TIMEOUT_S)
        assert eng.last_source == "neural"
    finally:
        eng.stop()


class _Beeper:
    available = True

    def __init__(self, log: list) -> None:
        self.log = log

    def preload(self, volume=100): pass

    def play(self, tone="gank", volume=100):
        self.log.append(("chime", tone, time.perf_counter()))
        return True


def test_chime_then_voice_never_overlapping() -> None:
    log: list = []

    class B(_NeuralFake):
        def speak(self, text, purge, timeout=None):
            log.append(("voice", text, time.perf_counter()))

        def speak_urgent(self, text, purge):
            log.append(("voice", text, time.perf_counter()))

    eng = _engine(B(), _Beeper(log))
    try:
        eng.say("Dragon dans 20 secondes.", 0)
        assert eng.wait_idle(3.0)
        eng.say("Lee Sin arrive !", 1)
        assert eng.wait_idle(3.0)
        assert [(k, t) for k, t, _ in log] == [("chime", "objective"), ("voice", "Dragon dans 20 secondes."),
                                               ("chime", "warning"), ("voice", "Lee Sin arrive !")]
        assert log[1][2] - log[0][2] >= voice.tone_duration_s("objective") - 0.01     # voice after the chime
        eng.chime_before_voice = False
        eng.say("Recule vers ta tour.", 0)
        assert eng.wait_idle(3.0)
        assert log[-1][0] == "voice"
    finally:
        eng.stop()


def test_chime_for() -> None:
    assert voice.chime_for("Baron dans 20 secondes.", 0) == "objective"
    assert voice.chime_for("Lee Sin arrive !", 1) == "warning"
    assert voice.chime_for("Pense à rentrer.", 0) == "info"


# -- pre-generation ---------------------------------------------------------------------

def test_static_phrases_cover_siege_and_late_objectives() -> None:
    st = tts_neural.static_phrases()
    assert "Ta base est attaquée, défends !" in st
    from treeaicoach.objectives import announcement_text

    assert announcement_text("baron", 17) in st


def test_prune_cache(tmp_path: Path) -> None:
    d = tmp_path / "fr-FR-DeniseNeural"
    d.mkdir()
    for i in range(10):
        p = d / f"{i}.wav"
        p.write_bytes(b"x" * 200_000)
        os.utime(p, (1000 + i, 1000 + i))
    removed = tts_neural.prune_cache(tmp_path, max_mb=1.0)
    left = sorted(p.name for p in d.iterdir())
    assert removed >= 5 and "9.wav" in left and "0.wav" not in left
    assert tts_neural.prune_cache(tmp_path, max_mb=0.1) == 0        # once per process and root
