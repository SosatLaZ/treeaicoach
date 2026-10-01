"""Gank alert latency benchmark (demo scenario through the REAL engine, accelerated clock).

Measures, for the demo gank (Lee Sin comes back from the fog in the top river at 33 s and
ganks me, Garen, top lane):

(a) -> (b): from the ground-truth moment the enemy jungler enters the warn radius / the danger
    radius (``DemoSource.positions``) to the ``VoiceEngine.say()`` call of the first gank alert of
    that level (simulated time + the real CPU time of the analysis tick that produced it),
    averaged over several tick phases;
(b) -> (c): from ``say()`` to the start of the audio (beep / sentence) in the real
    :class:`~treeaicoach.voice.VoiceEngine` worker with the real ``NeuralBackend`` (fake
    network synthesis, fake ``winsound``), cached vs not cached, and with a SAPI-like backend.

Usage::

    python tools/latency_bench.py [--fps 12] [--phases 4]

Prints one table; :func:`run` returns the numbers (``tests/test_latency.py``).
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GANK_KINDS = ("jungler_approach", "roam_approach", "collapse")


class _RecVoice:
    backend = "bench"

    def __init__(self, clock: Any) -> None:
        self.clock = clock
        self.said: list[tuple[float, str, int]] = []

    def say(self, text: str, level: int = 1) -> None:
        self.said.append((self.clock(), text, int(level)))

    def set_muted(self, on: bool) -> None:
        pass

    def prewarm(self, phrases: Any) -> None:
        pass

    def prefetch(self, *a: Any, **k: Any) -> None:
        pass


def ground_truth(src: Any, warn: float, danger: float, start: float = 30.0, end: float = 47.0,
                 step: float = 0.005) -> dict[str, float | None]:
    """First scenario second (after ``start``) at which the visible enemy jungler is inside
    the warn / danger radius of my champion, plus its fog reappearance time."""
    from treeaicoach.demo import CHAMPIONS, jungler_alias

    lee = jungler_alias()
    me = next(c.alias for c in CHAMPIONS if c.relation == "self")
    out: dict[str, float | None] = {"appear": None, "warn": None, "danger": None}
    s = start
    while s < end:
        pos = src.positions(s)
        u, v, vis = pos[lee]
        if vis:
            if out["appear"] is None:
                out["appear"] = s
            d = math.dist((u, v), pos[me][:2])
            if out["warn"] is None and d < warn:
                out["warn"] = s
            if out["danger"] is None and d < danger:
                out["danger"] = s
                break
        s += step
    return out


def detect_latency(fps: float = 12.0, phases: int = 4, end_s: float = 44.0,
                   cfg: Any = None) -> dict[str, Any]:
    """(a) -> (b) latencies (seconds), averaged over ``phases`` tick phases."""
    from treeaicoach.config import Config
    from treeaicoach.demo import DemoSource
    from treeaicoach.engine import CoachEngine

    cfg = cfg or Config(target_fps=fps)
    warn, danger = float(cfg.effective_warn_radius()), float(cfg.effective_danger_radius())
    runs: list[dict[str, Any]] = []
    tick_ms: list[float] = []
    for k in range(max(1, phases)):
        phase = (k / max(1, phases)) / fps
        now = [0.0]
        src = DemoSource(size=280)
        voice = _RecVoice(lambda: now[0])
        eng = CoachEngine(cfg, voice, frame_source=src, clock=lambda: now[0],
                          enable_hotkeys=False, manage_overlay=False)
        gt = ground_truth(src, warn, danger)
        first: dict[str, float | None] = {"warn": None, "danger": None, "pre": None}
        texts: list[tuple[float, str, int]] = []
        i = 0
        while True:
            t = 0.0 if i == 0 else phase + i / fps
            if t > end_s:
                break
            now[0] = t
            n_before = len(voice.said)
            t0 = time.perf_counter()
            said = eng.step(t)
            cpu = time.perf_counter() - t0
            if i > 0:
                tick_ms.append(cpu * 1000.0)
            for a in said:
                kind = getattr(a.kind, "value", str(a.kind))
                lvl = int(a.level)
                when = t + cpu       # say() is reached at the end of (or during) the tick
                texts.append((t, a.text, lvl))
                if kind in GANK_KINDS:
                    if lvl >= 1 and first["warn"] is None:
                        first["warn"] = when
                    if lvl >= 2 and first["danger"] is None:
                        first["danger"] = when
            # spoken outside the throttler (pre-alert "Lee Sin !")
            for ts, text, lvl in voice.said[n_before:]:
                if first["pre"] is None and gt["appear"] is not None and ts >= gt["appear"] \
                        and text.endswith(" !") and text[:-2] in ("Lee Sin", "LeeSin"):
                    first["pre"] = t + cpu
            i += 1
        runs.append({"gt": gt, "first": first, "texts": [x for x in texts if x[0] >= 30.0]})
    def lat(key: str, gt_key: str) -> list[float]:
        out = []
        for r in runs:
            a, b = r["gt"][gt_key], r["first"][key]
            if a is not None and b is not None:
                out.append(b - a)
        return out
    warn_l = lat("warn", "warn")
    danger_l = lat("danger", "danger")
    pre_l = lat("pre", "appear")
    return {
        "fps": fps, "gt": runs[0]["gt"], "warn": warn_l, "danger": danger_l, "pre": pre_l,
        "tick_ms_mean": statistics.fmean(tick_ms) if tick_ms else 0.0,
        "tick_ms_p95": sorted(tick_ms)[int(0.95 * (len(tick_ms) - 1))] if tick_ms else 0.0,
        "texts": runs[0]["texts"],
    }


# ------------------------------------------------------------------------- playback
class _FakeSapi:
    """SAPI-like local backend: records when speech starts (instantaneous, async)."""

    name = "sapi"

    def __init__(self, log: list) -> None:
        self.log = log
        self._until = 0.0

    def configure(self, *a: Any) -> None:
        pass

    def speak(self, text: str, purge: bool) -> None:
        self.log.append(("sapi", time.perf_counter(), text))
        self._until = time.perf_counter() + 0.8

    def is_speaking(self) -> bool:
        return time.perf_counter() < self._until

    def purge(self) -> None:
        self.log.append(("purge", time.perf_counter(), ""))
        self._until = 0.0

    def beep(self, volume: int) -> float:
        self.log.append(("beep", time.perf_counter(), ""))
        from treeaicoach.voice import BEEP_DURATION_S

        return BEEP_DURATION_S + 0.02

    def voices(self) -> list[str]:
        return []

    def pump(self) -> None:
        pass

    def close(self) -> None:
        pass


def _wav_bytes(seconds: float = 0.6) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x01" * int(24000 * seconds))
    return buf.getvalue()


def playback_latency(backend: str, cached: bool, level: int, network_s: float = 0.8,
                     repeats: int = 3, busy: bool = False) -> tuple[float, float]:
    """Median seconds from ``VoiceEngine.say()`` to (the first audio: beep or sentence,
    the start of the spoken sentence)."""
    from treeaicoach import tts_neural
    from treeaicoach.voice import NeuralBackend, VoiceEngine

    text = "Gank ! Lee Sin, recule !" if level >= 2 else "Lee Sin arrive par la rivière !"
    out: list[float] = []
    out_sentence: list[float] = []
    for _ in range(repeats):
        log: list = []
        with tempfile.TemporaryDirectory() as tmp:
            def synth(_t: str, _v: str, _r: int) -> bytes:
                time.sleep(network_s)
                return _wav_bytes()

            if backend == "neural":
                tts = tts_neural.NeuralTTS(cache_root=Path(tmp), _synth=synth)
                if cached:
                    p = tts.path_for(text)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(_wav_bytes())

                def play(path: str | None, _log: list = log) -> None:
                    _log.append(("play" if path else "stop", time.perf_counter(), path or ""))

                player = tts_neural.WavPlayer(_play=play)

                def factory(_log: list = log, _tts: Any = tts, _player: Any = player) -> Any:
                    return NeuralBackend(_tts=_tts, _player=_player,
                                         _local_factory=lambda: _FakeSapi(_log))
            else:
                def factory(_log: list = log) -> Any:
                    return _FakeSapi(_log)
            v = VoiceEngine(_backend_factory=factory)
            v.start()
            v.wait_ready(3.0)
            time.sleep(0.3)         # idle worker (the realistic case)
            if busy:                # a macro tip is being spoken when the gank comes
                v.say("Conseil : regarde la minimap.", 0)
                time.sleep(0.15)
                del log[:]
            t0 = time.perf_counter()
            v.say(text, level)
            deadline = t0 + 3.0
            first = sentence = None
            while time.perf_counter() < deadline and sentence is None:
                for kind, ts, x in list(log):
                    if kind in ("play", "sapi", "beep"):
                        if first is None:
                            first = ts - t0
                        is_beep = kind == "beep" or (kind == "play" and not str(x).startswith(tmp))
                        if not is_beep:
                            sentence = ts - t0
                            break
                time.sleep(0.002)
            v.stop()
            out.append(first if first is not None else float("inf"))
            out_sentence.append(sentence if sentence is not None else float("inf"))
    return statistics.median(out), statistics.median(out_sentence)


def run(fps: float | None = None, phases: int = 4, playback: bool = True) -> dict[str, Any]:
    from treeaicoach.config import Config

    fps = float(fps if fps is not None else Config().target_fps)
    res = detect_latency(fps=fps, phases=phases)
    if playback:
        res["playback"] = {
            (b, c, lvl): playback_latency(b, c, lvl)
            for b, c in (("neural", True), ("neural", False), ("sapi", True))
            for lvl in (1, 2)
        }
        res["busy"] = {lvl: playback_latency("neural", True, lvl, busy=True) for lvl in (1, 2)}
    return res


def _fmt(xs: list[float]) -> str:
    if not xs:
        return "  n/a (no alert)"
    return f"{statistics.fmean(xs):5.2f} s (min {min(xs):.2f}, max {max(xs):.2f})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fps", type=float, default=None)
    ap.add_argument("--phases", type=int, default=4)
    ap.add_argument("--no-playback", action="store_true")
    args = ap.parse_args(argv)
    import logging

    logging.basicConfig(level=logging.ERROR)
    res = run(args.fps, args.phases, not args.no_playback)
    gt = res["gt"]
    print(f"analysis fps {res['fps']:.0f}; tick CPU mean {res['tick_ms_mean']:.1f} ms, "
          f"p95 {res['tick_ms_p95']:.1f} ms")
    print(f"ground truth: appears {gt['appear']:.2f} s, enters warn radius {gt['warn']:.2f} s, "
          f"danger radius {gt['danger']:.2f} s")
    print(f"pre-alert  (appear -> say)       : {_fmt(res['pre'])}")
    print(f"WARNING    (warn radius -> say)  : {_fmt(res['warn'])}")
    print(f"DANGER     (danger radius -> say): {_fmt(res['danger'])}")
    pb = res.get("playback") or {}
    for (b, c, lvl), (v, vs) in pb.items():
        label = f"{b}{' cached' if c and b == 'neural' else ' not cached' if b == 'neural' else ''}"
        print(f"say -> audio start / sentence start  {label:18s} {'DANGER ' if lvl >= 2 else 'WARNING'}: "
              f"{v:.3f} s / {vs:.3f} s")
    for (b, c, lvl), (v, vs) in pb.items():
        base = res["danger"] if lvl >= 2 else res["warn"]
        if base:
            label = f"{b}{' cached' if c and b == 'neural' else ' not cached' if b == 'neural' else ''}"
            m = statistics.fmean(base)
            print(f"TOTAL radius -> audio / sentence  {label:18s} {'DANGER ' if lvl >= 2 else 'WARNING'}: "
                  f"{m + v:.2f} s / {m + vs:.2f} s")
    for lvl, (v, vs) in (res.get("busy") or {}).items():
        print(f"say -> audio while a tip is spoken (neural cached) {'DANGER ' if lvl >= 2 else 'WARNING'}: "
              f"{v:.3f} s / {vs:.3f} s")
    print("alerts said from 30 s:")
    for t, text, lvl in res["texts"]:
        print(f"  {t:6.2f} [{lvl}] {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
