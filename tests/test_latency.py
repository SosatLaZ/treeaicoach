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
