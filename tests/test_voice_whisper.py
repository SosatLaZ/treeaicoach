"""Local Whisper (mocked), Silero VAD segmentation, microphone selection / diagnosis, Whisper wake."""

from __future__ import annotations

import hashlib
import io
import threading
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from treeaicoach import audio_devices as ad
from treeaicoach import discord_listen as dl
from treeaicoach import stt_local
from treeaicoach import vad
from treeaicoach import voice_assistant as va
from treeaicoach.paths import asset_path

BLOCK = va.BLOCK_FRAMES


def _clip() -> bytes:
    with wave.open(str(asset_path("sounds", "voice", "baron_dans_30_secondes.wav"))) as w:
        pcm = w.readframes(w.getnframes())
        return va.resample_to_16k(pcm, w.getframerate(), w.getnchannels())


def _noise(s: float, sd: float = 40.0, seed: int = 0) -> bytes:
    return np.random.default_rng(seed).normal(0, sd, int(16000 * s)).astype("<i2").tobytes()


def _blocks(pcm: bytes) -> list[bytes]:
    n = BLOCK * 2
    pcm += b"\0" * (-len(pcm) % n)
    return [pcm[i:i + n] for i in range(0, len(pcm), n)]


# ------------------------------------------------------------------ Whisper wake
@pytest.mark.parametrize("text,wake,rest", [
    ("Hey coach, quel objet je fais ?", True, "quel objet je fais ?"),
    ("Hé, Coach ! On fait le drake ?", True, "On fait le drake ?"),
    ("Et coatch quel build", True, "quel build"),
    ("Hey Tree, on push ?", True, "on push ?"),
    ("Coach, il est où le jungler ?", True, "il est où le jungler ?"),
    ("Coach est nul", False, ""),
    ("Il faut cocher la case", False, ""),
    ("Et donc on fait quoi", False, ""),
    ("", False, ""),
])
def test_whisper_wake_fuzzy(text: str, wake: bool, rest: str) -> None:
    assert va.whisper_wake(text) == (wake, rest)


def test_wakeish_only_coach_like_words() -> None:
    assert va.wakeish("et coche") and va.wakeish("hey tri") and not va.wakeish("on va au dragon")


# ------------------------------------------------------------------ stt_local (no real model)
def test_features_and_prompt() -> None:
    w = stt_local.mel_filters()
    assert w.shape == (80, 201) and np.all(w >= 0) and w.sum() > 0
    f = stt_local.log_mel(stt_local.pcm_to_float(_clip()))
    assert f.shape == (80, 3000) and f.dtype == np.float32 and np.isfinite(f).all()
    p = stt_local.build_prompt(["Garen", "Ahri", "Ahri", ""])
    assert "Hey coach" in p and "gank" in p and p.count("Ahri") == 1 and p.count("Garen") == 1


def test_hallucinations_dropped() -> None:
    assert stt_local.clean_text("Sous-titres réalisés par la communauté d'Amara.org") == ""
    assert stt_local.clean_text("  quel   objet ") == "quel objet"


def test_backend_injected_and_cut_to_15s() -> None:
    seen = []
    w = stt_local.WhisperSTT(backend=lambda pcm, prompt: seen.append((len(pcm), prompt)) or " Quel objet ? ")
    assert w.ready() and w.transcribe(b"\1\0" * 16000 * 20) == "Quel objet ?"
    assert seen[0][0] == 16000 * 15 * 2 and "Baron" in seen[0][1]
    assert w.transcribe(b"") == ""


class _Resp(io.BytesIO):
    headers = {"Content-Length": "0"}

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *a) -> None:  # noqa: ANN002
        self.close()


def test_download_is_sha_pinned(tmp_path) -> None:  # noqa: ANN001
    data = b"model bytes"
    good = hashlib.sha256(data).hexdigest()
    assert stt_local._fetch("https://x/model.bin", good, tmp_path / "a.bin", opener=lambda u: _Resp(data))
    assert (tmp_path / "a.bin").read_bytes() == data
    assert not stt_local._fetch("https://x/model.bin", "0" * 64, tmp_path / "b.bin", opener=lambda u: _Resp(data))
    assert not (tmp_path / "b.bin").exists() and not (tmp_path / "b.bin.part").exists()
    for name, (rev, _mb, files) in stt_local.MODELS.items():
        assert len(rev) == 40 and set(files) == {"config.json", "tokenizer.json", "vocabulary.txt", "model.bin"}
        assert all(len(h) == 64 for h in files.values())
    assert all(len(sha) == 64 and url.startswith("https://files.pythonhosted.org/") for _p, url, sha in stt_local.WHEELS)


def test_load_failure_falls_back(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(stt_local, "import_engine", lambda root=None: None)
    w = stt_local.WhisperSTT(root=tmp_path)
    assert not w.load() and not w.ready() and "Vosk" in w.status and not w.available_soon()
    assert w.transcribe(b"\0\0" * 1600) == ""


# ------------------------------------------------------------------ VAD
def test_silero_cuts_two_utterances_and_ignores_noise() -> None:
    clip = _clip()
    stream = _noise(1.0) + clip + _noise(1.5, seed=1) + clip + _noise(1.0, seed=2)
    seg = vad.Segmenter()
    assert seg.kind == "silero"
    outs: list[bytes] = []
    for b in _blocks(stream):
        outs += seg.feed(b)
    assert len(outs) == 2
    for u in outs:
        assert len(clip) <= len(u) <= len(clip) + 2 * 16000          # the clip + pre-roll / tail, not the noise
    seg2 = vad.Segmenter()
    assert sum(len(seg2.feed(b)) for b in _blocks(_noise(5.0, sd=80, seed=3))) == 0


class _Scripted:
    def __init__(self, probs: list[float]) -> None:
        self.probs = list(probs)

    def reset(self) -> None:
        pass

    def prob(self, _f) -> float:  # noqa: ANN001
        return self.probs.pop(0) if self.probs else 0.0


def test_segmenter_logic_min_max_and_flush() -> None:
    frame = b"\1\0" * vad.FRAME
    seg = vad.Segmenter(_Scripted([0.0] * 5 + [0.9] * 20 + [0.0] * 30))
    outs = [u for _ in range(55) for u in seg.feed(frame)]
    assert len(outs) == 1 and not seg.in_speech
    seg = vad.Segmenter(_Scripted([0.9] * 3 + [0.0] * 30))             # a click: too short
    assert [u for _ in range(33) for u in seg.feed(frame)] == []
    seg = vad.Segmenter(_Scripted([0.9] * 1000), max_s=2.0)            # never stops: cut at max
    outs = [u for _ in range(200) for u in seg.feed(frame)]
    assert outs and all(len(u) <= int(2.0 * 16000 * 2) + vad.FRAME * 2 * 12 for u in outs)
    seg = vad.Segmenter(_Scripted([0.9] * 20))
    for _ in range(20):
        seg.feed(frame)
    assert seg.in_speech and seg.open_audio() and seg.flush() and not seg.in_speech


def test_energy_fallback() -> None:
    seg = vad.Segmenter(vad.EnergyVAD())
    loud = (np.sin(np.arange(16000) / 5) * 6000).astype("<i2").tobytes()
    outs = [u for b in _blocks(_noise(1.0) + loud + _noise(1.5, seed=4)) for u in seg.feed(b)]
    assert len(outs) == 1


# ------------------------------------------------------------------ devices
class _SD:
    def __init__(self) -> None:
        self.default = SimpleNamespace(device=(0, 3))

    def query_hostapis(self) -> list[dict]:
        return [{"name": "MME", "default_input_device": 0},
                {"name": "Windows WASAPI", "default_input_device": 4}]

    def query_devices(self) -> list[dict]:
        d = lambda n, h, ch=2: {"name": n, "hostapi": h, "max_input_channels": ch, "default_samplerate": 44100}  # noqa: E731
        return [d("Mappeur de sons Microsoft - Input", 0), d("Microphone (Realtek(R) Audio)", 0),
                d("Microphone (HyperX Cloud Stinge", 0), d("Haut-parleurs", 0, 0),
                d("Microphone (HyperX Cloud Stinger Core Wireless)", 1)]


def test_inputs_listed_and_windows_default_matched(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(ad.sys, "platform", "win32")
    sd = _SD()
    names = [d["name"] for d in ad.list_inputs(sd)]
    assert names == ["Microphone (Realtek(R) Audio)", "Microphone (HyperX Cloud Stinge"]
    # WASAPI's default (full name) -> its truncated MME twin, not the « Mappeur » nor the first mic
    assert ad.default_input_name(sd) == "Microphone (HyperX Cloud Stinge"
    assert ad.resolve("", sd) == (2, "Microphone (HyperX Cloud Stinge")
    assert ad.resolve("Microphone (Realtek(R) Audio)", sd) == (1, "Microphone (Realtek(R) Audio)")


class _FakeMic:
    def __init__(self, blocks: list[bytes], rate: int = 48000) -> None:
        self.blocks, self.rate = list(blocks), rate

    def read(self) -> bytes | None:
        return self.blocks.pop(0) if self.blocks else b"\0\0" * BLOCK

    def close(self) -> None:
        pass


def test_measure_explain_and_pick() -> None:
    zero = ad.measure(1, opener=lambda i: _FakeMic([b"\0\0" * BLOCK] * 4))
    assert zero["all_zero"] and "Confidentialité" in ad.explain(zero) and zero["rate"] == 48000
    loud = ad.measure(2, opener=lambda i: _FakeMic([np.full(BLOCK, 3000, "<i2").tobytes()] * 4))
    assert not loud["all_zero"] and loud["db"] > -21 and ad.explain(loud) == ""
    faint = ad.measure(3, opener=lambda i: _FakeMic([np.full(BLOCK, 10, "<i2").tobytes()] * 4))
    assert "ne capte presque rien" in ad.explain(faint)

    def busy(i):  # noqa: ANN001, ANN202
        raise OSError("Error opening InputStream: Device unavailable [PaErrorCode -9985]")
    ex = ad.measure(4, opener=busy)
    assert "exclusif" in ad.explain(ex)
    assert "Confidentialité" in ad.explain({"error": "E_ACCESSDENIED"})
    best = ad.pick_best([dict(zero, name="a"), dict(loud, name="b"), dict(faint, name="c"), dict(ex, name="d")])
    assert best["name"] == "b"
    assert ad.pick_best([dict(zero, name="a")]) is None


# ------------------------------------------------------------------ assistant with Whisper
class _STT:
    def __init__(self, text: str) -> None:
        self.text, self.calls, self.status = text, [], "prêt"

    def ready(self) -> bool:
        return True

    def transcribe(self, pcm: bytes, prompt=None) -> str:  # noqa: ANN001
        self.calls.append(len(pcm))
        return self.text if pcm else ""


def _check(a: va.VoiceAssistant) -> tuple[str, bool]:
    got = []
    ev = threading.Event()
    assert a.mic_check(lambda m, ok: (got.append((m, ok)), ev.set()), seconds=4.0)
    assert ev.wait(10.0)
    return got[0]


def test_mic_check_whisper_transcript_device_and_level() -> None:
    mic = _FakeMic(_blocks(_noise(0.5) + _clip() + _noise(1.0)), rate=48000)
    stt = _STT("Hey coach, quel objet ?")
    a = va.VoiceAssistant(mic_factory=lambda n: mic, recognizer_factory=lambda g: None, stt=stt)
    a.mic_name = "Casque"
    msg, ok = _check(a)
    assert ok and "« Hey coach, quel objet ? »" in msg and "Whisper" in msg and "« Casque »" in msg and "dB" in msg
    assert "Hey coach" in msg and stt.calls and stt.calls[0] < 4 * 16000 * 2      # only the VAD-cut speech


def test_mic_check_zero_mic_explains_privacy_and_picks_the_live_one() -> None:
    mic = _FakeMic([b"\0\0" * BLOCK] * 20)
    probe = lambda: [{"name": "Casque", "db": -96.0, "all_zero": True, "error": ""},  # noqa: E731
                     {"name": "Webcam", "db": -25.0, "all_zero": False, "error": ""}]
    a = va.VoiceAssistant(mic_factory=lambda n: mic, recognizer_factory=lambda g: None, stt=_STT(""), probe=probe)
    a.mic_name = "Casque"
    msg, ok = _check(a)
    assert not ok and "tout est à zéro" in msg and "Confidentialité" in msg and "« Webcam »" in msg
    assert a.suggested_mic == "Webcam" and a.snapshot()["suggested_mic"] == "Webcam"


def test_question_after_wake_goes_to_whisper() -> None:
    stt = _STT("quel objet je fais contre Darius")
    a = va.VoiceAssistant(mic_factory=lambda n: None, recognizer_factory=lambda g: None, stt=stt)
    asked = []
    a.on_question = asked.append
    mic = _FakeMic(_blocks(_noise(0.3) + _clip() + _noise(1.5)))
    a._question(mic, (None, ["quel"], ""))
    assert asked == ["quel objet je fais contre Darius"] and len(stt.calls) == 1


def test_whisper_wake_check_is_filtered_and_capped() -> None:
    stt = _STT("Hé coach on fait le Baron ?")
    a = va.VoiceAssistant(mic_factory=lambda n: None, recognizer_factory=lambda g: None, stt=stt)
    asked = []
    a.on_question = asked.append
    utt = _clip()
    assert not a._whisper_wake_check(_FakeMic([]), utt, "on va au dragon")        # not coach-like: no CPU
    assert stt.calls == []
    assert a._whisper_wake_check(_FakeMic([]), utt, "et coche on fait")
    assert asked == ["on fait le Baron ?"] and a.wakes == 1
    stt.text = "bonjour"
    for _ in range(10):
        a._whisper_wake_check(_FakeMic([]), utt, "et coche")
    assert len(stt.calls) == va.WHISPER_WAKE_PER_MIN


def test_vosk_only_without_whisper_in_tests() -> None:
    a = va.VoiceAssistant(mic_factory=lambda n: None, recognizer_factory=lambda g: None)
    assert a._stt() is None and not a.whisper_ready()


# ------------------------------------------------------------------ Discord: Whisper re-reads wake + question
WAKE, WORDS = 1000, 2000


class _Rec:
    def __init__(self, grammar) -> None:  # noqa: ANN001
        self.wake, self._r = grammar is not None, ""

    def accept(self, pcm: bytes) -> bool:
        v = int(np.frombuffer(pcm, dtype="<i2")[0])
        if self.wake:
            self._r = "hé coach" if v == WAKE else ""
            return v == WAKE
        if v == WORDS:
            self._r = "quelle ité"
            return True
        return False

    def result(self) -> str:
        r, self._r = self._r, ""
        return r

    def partial(self) -> str:
        return ""

    def final(self) -> str:
        return ""

    def reset(self) -> None:
        pass


def test_discord_question_text_from_whisper() -> None:
    clock = [100.0]
    stt = _STT("Hey coach, quel item acheter ?")
    lst = dl.VoiceListener(make_rec=_Rec, clock=lambda: clock[0], threaded=False, stt=stt)
    lst.should_listen = lambda: True
    got = []
    lst.on_question = lambda u, q: got.append((u, q))
    lst.bot_user_id = "999"
    lst.step(("11", np.full(BLOCK, WAKE, "<i2").tobytes()))
    clock[0] += 0.5
    lst.step(("11", np.full(BLOCK, WORDS, "<i2").tobytes()))
    clock[0] += 3.0
    lst.step(None)
    assert got == [("11", "quel item acheter ?")] and len(stt.calls) == 1
