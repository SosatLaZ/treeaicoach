"""The « Hey coach » keyword spotter (kws.py) and its wiring (PC assistant, Discord speaker)."""
from __future__ import annotations

import hashlib
import io
import threading
from pathlib import Path

import numpy as np
import pytest

from treeaicoach import discord_listen as dl
from treeaicoach import kws
from treeaicoach import voice_assistant as va

ORIG_SHARED = kws.shared_model


class FakeModel:
    """Scores scripted per 80 ms frame (no ONNX)."""

    def __init__(self, scores=()):
        self.np = np
        self.lock = threading.Lock()
        self.quiet = np.zeros(96, np.float32)
        self.scores = list(scores)
        self.embeds = 0

    def mel(self, samples):
        return np.zeros((8, 32), np.float32)

    def embed(self, windows):
        self.embeds += 1
        return np.ones((len(windows), 96), np.float32)

    def score(self, feats):
        return np.array([self.scores.pop(0) if self.scores else 0.0], np.float32)


def loud(seconds):
    n = int(seconds * 16000)
    return (np.sin(np.arange(n) * 0.3) * 3000).astype("<i2").tobytes()


def test_two_frames_in_a_row_wake_once_then_refractory():
    m = FakeModel([0.1, 0.9, 0.2, 0.9, 0.95, 0.99, 0.99, 0.99])
    sp = kws.Spotter(m, threshold=0.8)
    fired = [sp.feed(loud(0.08)) for _ in range(8)]
    assert fired == [False, False, False, False, True, False, False, False]   # one spike is not a wake


def test_silence_costs_no_embedding():
    m = FakeModel([0.99] * 50)
    sp = kws.Spotter(m, threshold=0.5)
    for _ in range(30):
        assert not sp.feed(b"\x00\x00" * kws.CHUNK)                  # digital silence: gate closed
    assert m.embeds == 0 and sp.computed == 0


def test_any_block_size():
    m = FakeModel([0.9] * 40)
    sp = kws.Spotter(m, threshold=0.5)
    pcm = loud(0.5)
    woke = any([sp.feed(pcm[k:k + 100]) for k in range(0, len(pcm), 100)])
    assert woke and sp.frames == len(pcm) // (2 * kws.CHUNK)


def test_download_checks_sha256(tmp_path, monkeypatch):
    good, bad = b"model-bytes", b"tampered!!!"
    monkeypatch.setattr(kws, "FEATURE_MODELS", {"m.onnx": ("u", hashlib.sha256(good).hexdigest(), len(good))})
    assert not kws.download_features(tmp_path, opener=lambda u: io.BytesIO(bad))
    assert not (tmp_path / "m.onnx").exists() and not kws.features_ready(tmp_path)
    assert kws.download_features(tmp_path, opener=lambda u: io.BytesIO(good))
    assert kws.features_ready(tmp_path)


def test_pinned_downloads_are_the_openwakeword_feature_models():
    assert set(kws.FEATURE_MODELS) == {"melspectrogram.onnx", "embedding_model.onnx"}
    for url, sha, size in kws.FEATURE_MODELS.values():
        assert url.startswith("https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/")
        assert len(sha) == 64 and size > 500_000


def test_head_asset_is_shipped_and_small():
    import onnxruntime as ort

    p = kws.head_path()
    assert p.is_file() and p.stat().st_size < 1_000_000
    s = ort.InferenceSession(str(p), providers=["CPUExecutionProvider"])
    out = s.run(None, {s.get_inputs()[0].name: np.zeros((3, 16, 96), np.float32)})[0]
    assert out.shape[0] == 3 and np.all((out >= 0) & (out <= 1))


def test_unavailable_model_falls_back_to_vosk(monkeypatch):
    monkeypatch.setattr(kws, "shared_model", ORIG_SHARED)
    monkeypatch.setattr(kws, "features_ready", lambda folder=None: False)
    monkeypatch.setattr(kws, "download_features", lambda *a, **k: False)
    kws._FAILED_AT[0] = -1e9
    assert kws.shared_model(now=1e6) is None and "indisponible" in kws.STATUS[0]
    assert kws.shared_model(now=1e6 + 10) is None                     # no retry storm
    eng = va.make_wake_engine(lambda g: None, "pc")
    assert isinstance(eng, va.WakeEngine)
    eng2 = va.make_wake_engine(lambda g: None, "pc", kws_model=FakeModel())
    assert isinstance(eng2, va.KwsWakeEngine) and eng2.ready() and eng2.take_question() == (None, [], "")


def test_discord_speaker_wakes_on_kws_and_never_asks_whisper_for_the_wake():
    t = [0.0]
    asked = []

    class Stt:
        def transcribe(self, *a, **k):
            asked.append(1)
            return "hey coach"

    sp = dl.Speaker("1", lambda g: None, lambda: t[0], lambda: Stt(), kws_model=FakeModel([0.0] * 6 + [0.9] * 10))
    assert sp.kws
    evs = []
    pcm = loud(1.2)
    for k in range(0, len(pcm), 640):
        t[0] += 0.02
        evs += sp.feed(pcm[k:k + 640])
    assert ("wake", "") in evs
    sp.engine.reset()
    t[0] += 1.0
    sp.spurt = [loud(0.25)] * 4
    assert sp._whisper_wake_check() == [] and not asked
    assert sp.start_question() and isinstance(sp.q_rec, va.NullRecognizer)   # Whisper reads the question


def test_pc_assistant_uses_kws_and_follow_up_needs_no_wake(monkeypatch):
    """Wake by the spotter -> chime + « J'écoute » -> question read by Whisper -> answer spoken -> a second
    question within 8 s without the wake phrase."""
    clock = [0.0]
    blocks = []
    speaking = [False]

    class Mic:
        rate = 16000

        def read(self):
            clock[0] += va.BLOCK_S
            if not blocks:
                a.stop()
                return None
            b = blocks.pop(0)
            if b == "speak":
                speaking[0] = True
                return b"\x00\x00" * va.BLOCK_FRAMES
            if b == "quiet":
                speaking[0] = False
                return b"\x00\x00" * va.BLOCK_FRAMES
            return b

        def close(self):
            pass

    class Stt:
        def __init__(self):
            self.n = 0

        def ready(self):
            return True

        def available_soon(self):
            return True

        def transcribe(self, pcm, *a, **k):
            self.n += 1
            return "Hey coach, quel objet je fais ?" if self.n == 1 else "Et contre Zed alors ?"

    class Seg:
        """1 block of speech -> utterance at the next silent block."""
        def __init__(self):
            self.cur, self.in_speech = [], False

        def feed(self, b):
            if va.block_rms(b) > 100:
                self.cur.append(b)
                self.in_speech = True
                return []
            self.in_speech = False
            if self.cur:
                u, self.cur = b"".join(self.cur), []
                return [u]
            return []

        def flush(self):
            return None

        def reset(self):
            self.cur, self.in_speech = [], False

        def open_audio(self):
            return b"".join(self.cur)

    m = FakeModel([0.0] * 3 + [0.95] * 3 + [0.0] * 500)
    a = va.VoiceAssistant(mic_factory=lambda n: Mic(), clock=lambda: clock[0], stt=Stt(),
                          kws_model=m, recognizer_factory=lambda g: None)
    monkeypatch.setattr(a, "_segmenter", lambda: Seg())
    monkeypatch.setattr(va, "claim_wake", lambda src, now=None: True)
    qs, wakes, events = [], [], []
    a.set_hooks(should_listen=lambda: True, is_speaking=lambda: speaking[0], on_wake=lambda: wakes.append(1),
                on_question=qs.append)
    a.on_listen = lambda what, text: events.append((what, text))
    a.enabled = True
    sil = b"\x00\x00" * va.BLOCK_FRAMES
    blocks += [sil, loud(0.25), loud(0.25)] + [loud(0.25), sil, sil]   # « hey coach » + question + silence
    blocks += ["speak"] * 4 + ["quiet"] + [sil, loud(0.25), sil, sil]  # answer spoken, then « et contre Zed »
    a._run()
    assert wakes == [1]
    assert qs == ["quel objet je fais ?", "Et contre Zed alors ?"]
    assert ("listening", "") in events and ("transcript", "quel objet je fais ?") in events
    assert ("listening", "suite") in events
