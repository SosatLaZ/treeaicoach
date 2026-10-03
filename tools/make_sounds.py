"""Generate the coach's UI sounds into ``treeaicoach/assets/sounds/*.wav`` (bundled in the exe).

Soft FM / sine chimes with smooth envelopes and a short reverb tail (see
:mod:`treeaicoach.chimes` for the sound design). Prints, per sound: duration, peak / RMS level,
time to the first audible sample (latency inside the file), clicks (sample jumps), file size.

    python -m tools.make_sounds [--out DIR] [--check]
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from treeaicoach import chimes  # noqa: E402

DEFAULT_OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "treeaicoach", "assets", "sounds")


def stats(x: np.ndarray) -> dict[str, float]:
    sr = chimes.SAMPLE_RATE
    a = np.abs(x)
    first = int(np.argmax(a > 10 ** (-40 / 20))) if a.max() > 0 else 0
    peak_db = 20 * np.log10(max(a.max(), 1e-9))
    rms_db = 20 * np.log10(max(float(np.sqrt(np.mean(x[a > 1e-4] ** 2))) if (a > 1e-4).any() else 1e-9, 1e-9))
    jump = float(np.max(np.abs(np.diff(x)))) if len(x) > 1 else 0.0
    return {"dur_ms": 1000 * len(x) / sr, "first_ms": 1000 * first / sr, "peak_db": peak_db, "rms_db": rms_db,
            "max_step": jump, "end_abs": float(abs(x[-1]))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--check", action="store_true", help="render and report only, write nothing")
    a = ap.parse_args(argv)
    if not a.check:
        os.makedirs(a.out, exist_ok=True)
    total = 0
    print(f"{'son':16s} {'durée':>7s} {'1er son':>8s} {'crête':>7s} {'RMS':>7s} {'saut max':>8s} {'taille':>8s}")
    for name in chimes.SOUNDS:
        x = chimes.render(name)
        data = chimes.to_wav(x, 100)
        st = stats(x)
        total += len(data)
        print(f"{name:16s} {st['dur_ms']:6.0f}ms {st['first_ms']:6.1f}ms {st['peak_db']:6.1f}dB {st['rms_db']:6.1f}dB "
              f"{st['max_step']:8.3f} {len(data) / 1024:6.1f}Ko")
        if not a.check:
            with open(os.path.join(a.out, f"{name}.wav"), "wb") as fh:
                fh.write(data)
    print(f"total {total / 1024:.1f} Ko")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
