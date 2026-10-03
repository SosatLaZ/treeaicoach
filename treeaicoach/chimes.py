"""Soft UI sounds of the coach (danger / warning / info / objective chimes, play-rating tones).

Each sound is a few FM bell / sine notes with a smooth envelope (raised-cosine attack of a few
ms, exponential decay, faded release: no click), a short gentle reverb tail (small Schroeder
reverb), a gentle low-pass, and a loudness normalisation per family (danger loudest, info
quietest). ``tools/make_sounds.py`` writes them to ``assets/sounds/*.wav`` (bundled in the exe);
:func:`wav_bytes` renders the same sound at run time when an asset is missing.

numpy only, never raises from :func:`wav_bytes` / :func:`load` (``b""`` on error).
"""

from __future__ import annotations

import io
import logging
import wave
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SAMPLE_RATE = 32000
LEAD_MS = 3                 # silence before the first note (some drivers clip the very first ms)


@dataclass(frozen=True)
class Note:
    freq: float             # Hz
    start: float            # s from the start of the sound
    dur: float              # s until the release starts
    amp: float = 1.0
    ratio: float = 2.0      # FM modulator / carrier frequency ratio (2 = warm, 3.5 = bell)
    index: float = 1.2      # FM index at the attack (decays to 0: bright attack, pure tail)
    decay: float = 6.0      # exponential decay rate (1/s)


@dataclass(frozen=True)
class Sound:
    notes: tuple[Note, ...]
    peak_db: float          # peak level after normalisation (dBFS)
    reverb: float = 0.18    # wet mix
    tail: float = 0.18      # reverb tail kept after the last note (s)
    attack_ms: float = 4.0
    release_ms: float = 60.0


def _n(freq: float, start: float, dur: float, **kw: float) -> Note:
    return Note(freq, start, dur, **kw)


# Notes (Hz)
C5, D5, E5, F5, G5, A5, B5 = 523.25, 587.33, 659.25, 698.46, 783.99, 880.0, 987.77
C6, D6, E6, G6 = 1046.5, 1174.66, 1318.51, 1567.98
G4, D4, A4 = 392.0, 293.66, 440.0

#: name -> sound. Danger tones: noticeable at once (bright FM attack, -3 dBFS) but rounded;
#: the others are softer and shorter.
SOUNDS: dict[str, Sound] = {
    # gank coming: two quick rising bell notes (a fourth), the classic "attention"
    "gank": Sound((_n(A5, 0.0, 0.07, ratio=3.5, index=1.2, decay=10.0),
                   _n(D6, 0.085, 0.10, ratio=3.5, index=1.2, decay=8.0)), peak_db=-3.0, tail=0.10,
                  release_ms=45.0),
    # personal danger ("Recule !"): three quick falling notes, lower
    "recule": Sound((_n(E6, 0.0, 0.05, ratio=2.0, index=1.4, decay=13.0),
                     _n(C6, 0.06, 0.05, ratio=2.0, index=1.4, decay=13.0),
                     _n(A5, 0.12, 0.09, ratio=2.0, index=1.4, decay=9.0)), peak_db=-3.0, tail=0.10,
                    release_ms=45.0),
    # my base attacked: two low, longer, warm notes
    "siege": Sound((_n(G5, 0.0, 0.14, ratio=1.0, index=1.0, decay=5.0, amp=0.9),
                    _n(D5, 0.16, 0.20, ratio=1.0, index=1.0, decay=4.0)), peak_db=-3.5, tail=0.12,
                   release_ms=50.0),
    # a gank warning / a call the voice is about to say: one soft bell
    "warning": Sound((_n(E5, 0.0, 0.10, ratio=3.0, index=0.9, decay=9.0),
                      _n(B5, 0.06, 0.10, ratio=3.0, index=0.7, decay=9.0, amp=0.7)), peak_db=-9.0, tail=0.10,
                     release_ms=45.0),
    # an information (AI line, reminder): one very soft pluck
    "info": Sound((_n(G5, 0.0, 0.10, ratio=2.0, index=0.5, decay=11.0),), peak_db=-15.0, tail=0.12),
    # an objective soon: gentle rising arpeggio
    "objective": Sound((_n(C5, 0.0, 0.08, ratio=2.0, index=0.8, decay=8.0, amp=0.8),
                        _n(E5, 0.07, 0.08, ratio=2.0, index=0.8, decay=8.0, amp=0.85),
                        _n(G5, 0.14, 0.16, ratio=2.0, index=0.8, decay=6.0)), peak_db=-9.0, tail=0.16),
    # play ratings (fx_overlay.play_wav_path uses assets/sounds/play_<class>.wav when bundled)
    "play_brilliant": Sound((_n(C6, 0.0, 0.07, ratio=3.5, index=1.0, decay=9.0, amp=0.8),
                             _n(E6, 0.07, 0.07, ratio=3.5, index=1.0, decay=9.0, amp=0.85),
                             _n(G6, 0.14, 0.16, ratio=3.5, index=1.0, decay=6.0)), peak_db=-10.0, tail=0.2),
    "play_great": Sound((_n(A5, 0.0, 0.07, ratio=3.0, index=0.9, decay=9.0, amp=0.85),
                         _n(D6, 0.08, 0.14, ratio=3.0, index=0.9, decay=7.0)), peak_db=-11.0),
    "play_best": Sound((_n(B5, 0.0, 0.14, ratio=3.0, index=0.8, decay=7.0),), peak_db=-12.0),
    "play_good": Sound((_n(A5, 0.0, 0.12, ratio=2.0, index=0.6, decay=9.0),), peak_db=-14.0),
    "play_inaccuracy": Sound((_n(C5, 0.0, 0.08, ratio=1.0, index=0.5, decay=9.0),
                              _n(A4, 0.09, 0.12, ratio=1.0, index=0.5, decay=8.0)), peak_db=-14.0),
    "play_mistake": Sound((_n(A4, 0.0, 0.09, ratio=1.0, index=0.6, decay=8.0),
                           _n(F5 / 2, 0.10, 0.14, ratio=1.0, index=0.6, decay=7.0)), peak_db=-13.0),
    "play_blunder": Sound((_n(G4, 0.0, 0.09, ratio=1.0, index=0.7, decay=8.0),
                           _n(E5 / 2 * 1.189, 0.10, 0.09, ratio=1.0, index=0.7, decay=8.0),
                           _n(D4, 0.20, 0.16, ratio=1.0, index=0.7, decay=6.0)), peak_db=-12.0),
    "play_miss": Sound((_n(B5 / 2, 0.0, 0.09, ratio=1.0, index=0.5, decay=9.0),
                        _n(G4 * 1.06, 0.10, 0.12, ratio=1.0, index=0.5, decay=8.0)), peak_db=-14.0),
}
DANGER_TONES = ("gank", "recule", "siege")


def _render_note(np, note: Note, n_total: int, attack_s: float, release_s: float):  # noqa: ANN001
    sr = SAMPLE_RATE
    n = int(round((note.dur + release_s) * sr))
    t = np.arange(n, dtype=np.float64) / sr
    env = np.exp(-note.decay * t)
    a = max(1, int(attack_s * sr))
    env[:a] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(a) / a)          # raised-cosine attack
    r = max(1, int(release_s * sr))
    env[-r:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(r) / r)          # raised-cosine release
    idx = note.index * np.exp(-note.decay * 1.5 * t)                  # bright attack, pure tail
    mod = idx * np.sin(2 * np.pi * note.freq * note.ratio * t)
    sig = np.sin(2 * np.pi * note.freq * t + mod) + 0.12 * np.sin(4 * np.pi * note.freq * t)
    out = np.zeros(n_total)
    s0 = int(round(note.start * sr))
    m = min(n, n_total - s0)
    if m > 0:
        out[s0:s0 + m] = note.amp * env[:m] * sig[:m]
    return out


def _reverb(np, x, wet: float):  # noqa: ANN001
    """Small Schroeder reverb (4 combs + 2 all-passes), short and dark."""
    if wet <= 0:
        return x
    sr = SAMPLE_RATE
    acc = np.zeros_like(x)
    for ms, g in ((29.7, 0.72), (37.1, 0.70), (41.1, 0.68), (43.7, 0.66)):
        d = int(sr * ms / 1000)
        y = x.copy()
        for i in range(d, len(y), d):                 # block recursion: y[i] += g * y[i - d]
            j = min(len(y), i + d)
            y[i:j] += g * y[i - d:j - d]
        acc += y
    acc /= 4.0
    for ms, g in ((5.0, 0.6), (1.7, 0.6)):
        d = int(sr * ms / 1000)
        y = np.zeros_like(acc)
        buf = acc.copy()
        # all-pass y[n] = -g x[n] + x[n-d] + g y[n-d] (block form)
        for i in range(0, len(acc), d):
            j = min(len(acc), i + d)
            xd = buf[i - d:j - d] if i >= d else np.zeros(j - i)
            yd = y[i - d:j - d] if i >= d else np.zeros(j - i)
            y[i:j] = -g * buf[i:j] + xd + g * yd
        acc = y
    # one-pole low-pass on the wet signal (dark tail)
    lp = np.empty_like(acc)
    k = 0.35
    prev = 0.0
    for i in range(len(acc)):
        prev = prev + k * (acc[i] - prev)
        lp[i] = prev
    return (1.0 - wet) * x + wet * lp


def render(name: str):
    """Float samples (numpy array, peak-normalised) of ``name``. Raises on an unknown name."""
    import numpy as np  # noqa: PLC0415

    snd = SOUNDS[name]
    sr = SAMPLE_RATE
    attack_s, release_s = snd.attack_ms / 1000.0, snd.release_ms / 1000.0
    end = max(nt.start + nt.dur + release_s for nt in snd.notes)
    n_total = int(round((end + snd.tail) * sr))
    x = np.zeros(n_total)
    for nt in snd.notes:
        x += _render_note(np, nt, n_total, attack_s, release_s)
    # gentle low-pass on the dry signal (removes the harsh FM sidebands)
    y = np.empty_like(x)
    prev = 0.0
    for i in range(n_total):
        prev = prev + 0.55 * (x[i] - prev)
        y[i] = prev
    y = _reverb(np, y, snd.reverb)
    # final fade of the tail to exact zero (no click when cut / at the end)
    f = int(0.06 * sr)
    y[-f:] *= np.linspace(1.0, 0.0, f) ** 2
    peak = float(np.max(np.abs(y))) or 1.0
    y *= (10.0 ** (snd.peak_db / 20.0)) / peak
    lead = np.zeros(int(sr * LEAD_MS / 1000))
    return np.concatenate([lead, y])


def to_wav(samples, volume: int = 100) -> bytes:  # noqa: ANN001
    """16-bit mono WAV bytes of float samples scaled by ``volume`` %."""
    import numpy as np  # noqa: PLC0415

    v = max(0, min(100, int(volume))) / 100.0
    pcm = np.clip(np.round(np.asarray(samples) * v * 32767.0), -32768, 32767).astype("<i2")
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return bio.getvalue()


def asset_file(name: str) -> Path | None:
    try:
        from treeaicoach import paths  # noqa: PLC0415

        p = Path(paths.asset_path("sounds", f"{name}.wav"))
        return p if p.is_file() else None
    except Exception:
        return None


_CACHE: dict[str, object] = {}


def load(name: str):
    """Float samples of ``name``: the bundled asset if present, else rendered (cached). None on error."""
    try:
        if name in _CACHE:
            return _CACHE[name]
        import numpy as np  # noqa: PLC0415

        samples = None
        p = asset_file(name)
        if p is not None:
            with wave.open(str(p), "rb") as w:
                if w.getsampwidth() == 2 and w.getnchannels() == 1:
                    samples = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32767.0
                    if w.getframerate() != SAMPLE_RATE:
                        samples = None
        if samples is None and name in SOUNDS:
            samples = render(name)
        _CACHE[name] = samples
        return samples
    except Exception as exc:
        log.debug("chime %s unavailable: %s", name, exc)
        return None


def wav_bytes(name: str, volume: int = 100) -> bytes:
    """WAV bytes of ``name`` at ``volume`` %. ``b""`` when unavailable. Never raises."""
    try:
        s = load(name)
        return to_wav(s, volume) if s is not None else b""
    except Exception as exc:
        log.debug("chime %s failed: %s", name, exc)
        return b""


def duration_s(name: str) -> float:
    s = load(name)
    return (len(s) / SAMPLE_RATE) if s is not None else 0.0


__all__ = ["DANGER_TONES", "SAMPLE_RATE", "SOUNDS", "duration_s", "load", "render", "to_wav", "wav_bytes"]
