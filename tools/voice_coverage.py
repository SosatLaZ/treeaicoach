"""Voice prefetch coverage: which spoken lines would play from the neural cache.

Runs the ``tools/ux_replay.py`` scenarios with a voice stub that records the sentences the engine
asks to pre-generate (``VoiceEngine.prewarm``) and the sentences it says, then reports the share of
spoken lines that were pre-generated BEFORE they were said (= played instantly by the natural voice,
never by a Windows fallback voice). Pure simulation: no audio, no network.

    python -m tools.voice_coverage [--level all] [--scenario all] [-v]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import ux_replay  # noqa: E402


def _speakable(text: str) -> str:
    try:
        from treeaicoach.tts_lexicon import speakable  # noqa: PLC0415

        return speakable(text)
    except Exception:
        return text


class _CovVoice(ux_replay._Voice):
    def __init__(self) -> None:
        super().__init__()
        self.ready: set[str] = set()
        self.log: list[tuple[float, str, bool]] = []

    def prewarm(self, phrases: Any) -> None:
        self.ready.update(_speakable(p) for p in phrases or ())

    def say(self, text: str, level: int = 1) -> None:
        super().say(text, level)
        self.log.append((self.gt, str(text), _speakable(str(text)) in self.ready))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--level", default="all")
    ap.add_argument("--scenario", default="all")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    levels = list(ux_replay.LEVELS) if a.level == "all" else [a.level]
    names = list(ux_replay.SCENARIOS) if a.scenario == "all" else a.scenario.split(",")
    voices: list[_CovVoice] = []
    orig = ux_replay._Voice

    def factory() -> _CovVoice:
        v = _CovVoice()
        voices.append(v)
        return v

    ux_replay._Voice = factory          # type: ignore[assignment,misc]
    total = hit = 0
    misses: dict[str, int] = {}
    try:
        for lvl in levels:
            for name in names:
                n0 = len(voices)
                ux_replay.run(name, lvl)
                for v in voices[n0:]:
                    for _gt, text, ok in v.log:
                        total += 1
                        hit += ok
                        if not ok:
                            misses[text] = misses.get(text, 0) + 1
    finally:
        ux_replay._Voice = orig         # type: ignore[misc]
        logging.disable(logging.NOTSET)
    pct = 100.0 * hit / total if total else 100.0
    print(f"Lignes dites : {total} ; pré-générées avant d'être dites : {hit} ({pct:.1f} %)")
    if misses:
        print("Non pré-générées :")
        for text, n in sorted(misses.items(), key=lambda kv: -kv[1])[: (200 if a.verbose else 25)]:
            print(f"  {n:3d} x {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
