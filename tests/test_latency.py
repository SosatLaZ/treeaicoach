"""Gank alert latency: detection -> say() -> audio start (tools/latency_bench.py), pre-generated
gank sentences, urgent voice path. Targets: < 0.6 s from entering the danger radius to the audio
start (cached voice), < 1 s for the WARNING."""

from __future__ import annotations

import importlib.util
import io
import math
import sys
import time
import wave
from itertools import permutations
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from treeaicoach import tts_neural
from treeaicoach.alerts import AlertKind, Level, phrase
from treeaicoach.config import Config
from treeaicoach.gank import pre_alert_text
from treeaicoach.voice import NeuralBackend, VoiceEngine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import test_gank as G  # noqa: E402


def _bench():
    spec = importlib.util.spec_from_file_location("latency_bench", ROOT / "tools" / "latency_bench.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x01" * 12000)
    return buf.getvalue()


# ------------------------------------------------------------------ pre-generated phrases
def gank_phrases(names: list[str]) -> set[str]:
    """Every sentence gank.py can emit about these enemies (single threat, merged duo, merged
    with one anonymous enemy, pre-alert), at WARNING and DANGER, every direction / lane."""
    out: set[str] = set()
    directions = list(tts_neural.DIRECTIONS) + [None]
    lanes = ("top", "mid", "bot", None)
    for n in names:
        out.add(pre_alert_text(n))
        for kind in (AlertKind.JUNGLER_APPROACH, AlertKind.ROAM_APPROACH):
            out.add(phrase(kind, Level.DANGER, n))
            for d in directions:
                out.add(phrase(kind, Level.WARNING, n, d))
        for lvl in (Level.WARNING, Level.DANGER):
            for lane in lanes:
                out.add(phrase(AlertKind.COLLAPSE, lvl, None, lane, count=2, names=[n]))
    for a, b in permutations(names, 2):
        for lvl in (Level.WARNING, Level.DANGER):
            for lane in lanes:
                out.add(phrase(AlertKind.COLLAPSE, lvl, None, lane, count=2, names=[a, b]))
    return out


def test_every_gank_phrase_is_cached_after_prewarm(tmp_path: Path) -> None:
    names = ["Lee Sin", "Ahri", "Darius", "Caitlyn", "Nautilus"]
    game = NS(enemies=[NS(champion_name=n) for n in names], allies=[NS(champion_name="Garen")])
    tts = tts_neural.NeuralTTS(cache_root=tmp_path, _synth=lambda text, v, r: _wav())
    backend = NeuralBackend(_tts=tts, _player=tts_neural.WavPlayer(_play=lambda p: None),
                            _local_factory=lambda: None)
    phrases = tts_neural.build_phrase_list(game)
    backend.prewarm(phrases)
    wanted = gank_phrases(names)
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline and any(tts.cached(p) is None for p in wanted):
        time.sleep(0.05)
    missing = sorted(p for p in wanted if tts.cached(p) is None)
    assert not missing, missing[:10]
    # the most urgent ones first
    assert phrases[0] == "Gank ! Lee Sin, recule !" and phrases[1] == "Lee Sin !"


# ------------------------------------------------------------------ gank analyser
def test_jungler_popping_out_of_fog_close_pre_alert_then_sentence() -> None:
    appear = 2.0
    start = (G.ME_TOP[0] + 0.16, G.ME_TOP[1] + 0.03)        # d ~0.16: inside warn, outside danger

    def enemies(t: float) -> dict:
        if t < appear:
            return {}
        return {"LeeSin": G.lerp_path([start, G.ME_TOP], 0.025, t - appear)}

    ticks = G.simulate(5.0, enemies)
    said = [(tk.t, a.text, a.level) for tk in ticks for a in tk.said]
    assert said[0] == (pytest.approx(appear), "Lee Sin !", Level.WARNING)     # first frame
    full = [x for x in said if x[1].startswith("Lee Sin arrive")]
    assert full and full[0][0] - appear <= 1.0, said
    # without the pre-alert option: the full WARNING at once
    ticks = G.simulate(5.0, enemies, cfg=Config(gank_pre_alert=False))
    said = [(tk.t, a.text) for tk in ticks for a in tk.said]
    assert said[0][0] == pytest.approx(appear) and said[0][1].startswith("Lee Sin arrive"), said


def test_jungler_popping_out_of_fog_inside_danger_first_frame() -> None:
    p = (G.ME_TOP[0] + 0.08, G.ME_TOP[1] + 0.02)
    ticks = G.simulate(3.0, lambda t: {"LeeSin": p} if t >= 1.5 else {})
    first = next(tk for tk in ticks if tk.said)
    assert first.t == pytest.approx(1.5)
    assert first.said[0].level == Level.DANGER and first.said[0].text == "Gank ! Lee Sin, recule !"


def test_walking_approach_warning_fast() -> None:
    """A visible jungler walking towards me (from outside the warn radius): WARNING < 1 s after it
    enters the warn radius."""
    path = [(G.ME_TOP[0] + 0.30, G.ME_TOP[1] + 0.06), G.ME_TOP]
    ticks = G.simulate(10.0, lambda t: {"LeeSin": G.lerp_path(path, 0.025, t)})
    entered = next(tk.t for tk in ticks if math.dist(G.lerp_path(path, 0.025, tk.t), G.ME_TOP) < G.WARN)
    warn = next(tk.t for tk in ticks for a in tk.said if a.level == Level.WARNING)
    assert warn - entered < 1.0, (entered, warn)


# ------------------------------------------------------------------ voice
class _Local:
    name = "sapi"

    def __init__(self, log: list) -> None:
        self.log = log

    def configure(self, *a) -> None:
        pass

    def speak(self, text: str, purge: bool) -> None:
        self.log.append(("sapi", time.perf_counter(), text))

    def is_speaking(self) -> bool:
        return False

    def purge(self) -> None:
        pass

    def voices(self) -> list:
        return []

    def pump(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_uncached_gank_phrase_uses_local_voice_at_once_and_caches_it(tmp_path: Path) -> None:
    def slow(text: str, v: str, r: int) -> bytes:
        time.sleep(0.5)
        return _wav()

    log: list = []
    tts = tts_neural.NeuralTTS(cache_root=tmp_path, _synth=slow)
    backend = NeuralBackend(_tts=tts, _player=tts_neural.WavPlayer(_play=lambda p: None),
                            _local_factory=lambda: _Local(log))
    t0 = time.perf_counter()
    backend.speak_urgent("Lee Sin arrive par le haut !", False)
    assert log and log[0][0] == "sapi" and log[0][1] - t0 < 0.1
    deadline = time.monotonic() + 5.0
    while tts.cached("Lee Sin arrive par le haut !") is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert tts.cached("Lee Sin arrive par le haut !") is not None     # next time: neural voice


def test_bench_playback_and_interrupt() -> None:
    lb = _bench()
    for backend, cached in (("neural", True), ("neural", False), ("sapi", True)):
        first, sentence = lb.playback_latency(backend, cached, 1, repeats=1)
        assert sentence < 0.1, (backend, cached, sentence)              # WARNING: no network wait
        first, sentence = lb.playback_latency(backend, cached, 2, repeats=1)
        assert first < 0.1 and sentence < 0.45, (backend, cached, first, sentence)   # beep, then voice
    # a gank alert cuts a macro tip being spoken
    assert lb.playback_latency("neural", True, 1, repeats=1, busy=True)[1] < 0.1
    assert lb.playback_latency("neural", True, 2, repeats=1, busy=True)[0] < 0.1


def test_bench_demo_detection_latency() -> None:
    lb = _bench()
    res = lb.detect_latency(fps=Config().target_fps, phases=1)
    assert res["danger"] and res["danger"][0] < 0.6 - 0.05, res     # + ~0.001 s beep start
    assert res["warn"] and res["warn"][0] < 1.0, res
    assert res["tick_ms_mean"] < 1000.0 / Config().target_fps


# ------------------------------------------------------------------ beep-first danger alerts
class _Rec:
    """Fake in-memory audio output of the BeepPlayer (records the start time)."""

    def __init__(self) -> None:
        self.t: list[float] = []

    def __call__(self, data: bytes) -> None:
        assert data[:4] == b"RIFF"
        self.t.append(time.perf_counter())


class _FakeTTS:
    name = "fake"

    def __init__(self, cached: bool) -> None:
        self.cached = cached
        self.spoken: list[tuple[float, str]] = []
        self.prewarmed: list[str] = []
        self.beeps = 0

    def configure(self, *a) -> None:
        pass

    def ready(self, text: str) -> bool:
        return self.cached

    def prewarm(self, phrases) -> None:
        self.prewarmed += list(phrases)

    def speak(self, text: str, purge: bool) -> None:
        self.spoken.append((time.perf_counter(), text))

    def is_speaking(self) -> bool:
        return False

    def purge(self) -> None:
        pass

    def beep(self, volume: int) -> float:
        self.beeps += 1
        return 0.0

    def voices(self) -> list:
        return []

    def pump(self) -> None:
        pass

    def close(self) -> None:
        pass


def _beep_voice(cached: bool, mode: str = "bip_voix"):
    from treeaicoach.voice import BeepPlayer

    out = _Rec()
    tts = _FakeTTS(cached)
    v = VoiceEngine(_backend_factory=lambda: tts, _beep_player=BeepPlayer(_play=out), danger_voice=mode)
    v.start()
    assert v.wait_ready(3.0)
    return v, tts, out


@pytest.mark.parametrize("cached", [True, False])
def test_danger_is_beep_first_and_voice_only_if_cached(cached: bool) -> None:
    v, tts, out = _beep_voice(cached)
    try:
        t0 = time.perf_counter()
        v.say("Gank ! Lee Sin, recule !", 2)
        assert v.beeper.played and v.beeper.played[-1][1] - t0 < 0.01      # play() called synchronously
        deadline = time.monotonic() + 2.0
        while not out.t and time.monotonic() < deadline:
            time.sleep(0.001)
        assert out.t and out.t[0] - t0 < 0.05, out.t                        # detection -> beep < 50 ms
        assert v.wait_idle(3.0)
        time.sleep(0.4)
        assert tts.beeps == 0                                               # no second (backend) beep
        if cached:
            assert [x for _t, x in tts.spoken] == ["Gank ! Lee Sin, recule !"]
            assert tts.spoken[0][0] - t0 >= 0.2                             # after the beep, never before
            assert tts.spoken[0][0] - t0 < 0.6                              # detection -> voice
        else:
            assert tts.spoken == [] and v.beep_only_count == 1              # beep alone, no TTS wait
            assert tts.prewarmed == ["Gank ! Lee Sin, recule !"]            # ready next time
    finally:
        v.stop()


def test_danger_voice_setting_beep_only() -> None:
    v, tts, out = _beep_voice(True, mode="bip")
    try:
        v.say("Recule !", 2)
        assert v.wait_idle(3.0)
        time.sleep(0.3)
        assert out.t and tts.spoken == [] and v.beep_only_count == 1
        v.set_danger_voice("bip_voix")
        assert v.alert_beep("recule")                                      # engine path: tone first
        v.say("Recule !", 2)
        assert v.wait_idle(3.0)
        time.sleep(0.5)
        assert [x for _t, x in tts.spoken] == ["Recule !"]
        assert [tone for tone, _t in v.beeper.played][-1] == "recule"
        assert len(v.beeper.played) == 2                                    # say() did not beep twice
    finally:
        v.stop()


def test_tones_are_distinct_wavs() -> None:
    from treeaicoach.voice import TONES, tone_wav_bytes

    wavs = {t: tone_wav_bytes(t, 100) for t in TONES}
    assert set(wavs) == {"gank", "recule", "siege"}
    assert all(w[:4] == b"RIFF" and len(w) > 1000 for w in wavs.values())
    assert len(set(wavs.values())) == 3


def test_engine_detection_to_beep_under_50ms(tmp_path, monkeypatch) -> None:
    """Full engine tick: the 2 v 1 at 53 % HP (real case 3:24) is detected and the "recule" tone
    starts < 50 ms after the tick that sees it, before / without any TTS."""
    import numpy as np

    import test_danger as D
    from treeaicoach import paths
    from treeaicoach.detector import Detection
    from treeaicoach.engine import CoachEngine
    from treeaicoach.identifier import Identified

    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    paths._reset_cache()
    v, tts, out = _beep_voice(False)
    frame = (40 + np.random.default_rng(0).integers(0, 30, size=(200, 200, 3))).astype(np.uint8)

    class _Src:
        is_demo = False

        def next(self, t: float):
            g = D.game_at(204.0 + t, 567.0 / 1062.0, my_level=4, levels={"Darius": 4, "Ahri": 4})
            g.fetched_at = t
            return frame, g

    def ident_all(_frame):
        res = []
        for alias, rel, uv in (("Garen", "self", (0.13, 0.12)), ("Darius", "enemy", (0.145, 0.105)),
                               ("Ahri", "enemy", (0.15, 0.125))):
            if rel == "enemy" and clock[0] < 3.0:
                continue                          # warm-up ticks (lazy components) without enemies
            probs = (0.9, 0.05, 0.05) if rel == "enemy" else (0.05, 0.05, 0.9)
            det = Detection(u=uv[0], v=uv[1], r=0.03, score=0.95, cls=rel, cls_probs=probs, alias=alias)
            res.append(Identified(det=det, alias=alias, relation=rel, team="CHAOS" if rel == "enemy" else "ORDER",
                                  id_score=0.95))
        return res

    clock = [0.0]
    try:
        eng = CoachEngine(Config(), v, frame_source=_Src(), clock=lambda: clock[0], enable_hotkeys=False,
                          manage_overlay=False, recorder_factory=lambda: None)
        eng._vision = ident_all
        lat = None
        for i in range(40):
            clock[0] = i * 0.25
            n = len(v.beeper.played)
            t0 = time.perf_counter()
            eng.step(clock[0])
            if len(v.beeper.played) > n:
                lat = v.beeper.played[-1][1] - t0
                break
        assert lat is not None, "no danger beep"
        assert lat < 0.05, lat
        deadline = time.monotonic() + 1.0
        while not out.t and time.monotonic() < deadline:
            time.sleep(0.001)
        assert out.t and out.t[0] - t0 < 0.05 + 0.05
    finally:
        v.stop()
        paths._reset_cache()
