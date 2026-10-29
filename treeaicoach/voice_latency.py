"""Latency of one spoken question, end of speech -> first sound of the answer (3.4.1).

The chain crosses threads and modules (mic loop / Discord listener -> Whisper thread -> AI request ->
TTS -> playback), and only ONE question is answered at a time, so a single « current question » record
is enough. Each stage marks what it measured; :func:`playing` (the first sound of the answer) closes the
record and logs one line::

    Latence question : vad 450 ms · file 0 · whisper 720 · ia 900 · voix 600 · total 2700 ms

The last answers are kept for the analysis report (:func:`snapshot`). Thread-safe, never raises.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

log = logging.getLogger(__name__)

STAGES = ("vad", "file", "whisper", "ia", "voix")
#: a record not answered within this is abandoned (the answer was dropped / the AI failed)
GIVE_UP_S = 120.0
HISTORY = 20

_lock = threading.Lock()
_cur: dict[str, Any] | None = None
_hist: deque[dict[str, Any]] = deque(maxlen=HISTORY)
_wake: list[float] = [-1e9, 0.0]          # (when, ms) of the last wake confirmation
clock = time.monotonic


def _norm(text: Any) -> str:
    return " ".join(str(text or "").lower().split())[:40]


def begin(source: str, vad_ms: float = 0.0) -> None:
    """A question's audio was just cut by the VAD, ``vad_ms`` after its last spoken block (Silero's
    silence hangover). Starts a new record (a previous unanswered one is dropped)."""
    try:
        now = clock()
        vad_ms = max(0.0, float(vad_ms))
        with _lock:
            global _cur
            recent = now - _wake[0] < 30.0
            _cur = {"source": str(source), "t0": now - vad_ms / 1000.0, "t": now, "ms": {"vad": vad_ms},
                    "wake_ms": _wake[1] if recent else None, "expect": "", "asked": None, "answered": None}
    except Exception:
        log.debug("latency begin failed", exc_info=True)


def add(stage: str, ms: float) -> None:
    """``ms`` more for ``stage`` (« file » = waited for the Whisper thread, « whisper » = its compute)."""
    try:
        with _lock:
            if _cur is not None:
                _cur["ms"][stage] = _cur["ms"].get(stage, 0.0) + max(0.0, float(ms))
    except Exception:
        pass


def wake(ms: float) -> None:
    """The wake confirmation's Whisper pass (reported apart: it happens before the question)."""
    try:
        with _lock:
            _wake[0], _wake[1] = clock(), float(ms)
    except Exception:
        pass


def asked() -> None:
    """The question went to the AI (its request starts now)."""
    try:
        with _lock:
            if _cur is not None and _cur["asked"] is None:
                _cur["asked"] = clock()
    except Exception:
        pass


def answered(text: Any) -> None:
    """The AI answer arrived; ``text`` is what will be said (its first words match :func:`playing`)."""
    try:
        with _lock:
            if _cur is None or _cur["answered"] is not None:
                return
            now = clock()
            _cur["answered"] = now
            _cur["expect"] = _norm(text)
            if _cur["asked"] is not None:
                _cur["ms"]["ia"] = (now - _cur["asked"]) * 1000.0
    except Exception:
        pass


def playing(text: Any = None) -> dict[str, Any] | None:
    """The first sound of a sentence starts. When it is the expected answer (or ``text`` is None and an
    answer is pending), the record closes: logged + kept. Returns the closed record."""
    try:
        with _lock:
            global _cur
            cur = _cur
            if cur is None or cur["answered"] is None:
                return None
            if text is not None:
                n = _norm(text)
                exp = cur["expect"]
                if exp and n and not (exp.startswith(n[:20]) or n.startswith(exp[:20])):
                    return None
            now = clock()
            if now - cur["t0"] > GIVE_UP_S:
                _cur = None
                return None
            ms = cur["ms"]
            ms["voix"] = (now - cur["answered"]) * 1000.0
            rec = {"source": cur["source"], **{k: round(ms.get(k, 0.0)) for k in STAGES},
                   "total": round((now - cur["t0"]) * 1000.0),
                   "wake": None if cur["wake_ms"] is None else round(cur["wake_ms"]), "at": time.time()}
            _cur = None
            _hist.append(rec)
        log.info("Latence question : %s", line(rec))
        return rec
    except Exception:
        log.debug("latency playing failed", exc_info=True)
        return None


def line(rec: dict[str, Any]) -> str:
    return (f"vad {rec['vad']} ms · file {rec['file']} · whisper {rec['whisper']} · ia {rec['ia']} · "
            f"voix {rec['voix']} · total {rec['total']} ms ({rec['source']})")


def snapshot() -> dict[str, Any]:
    """The last answered questions (newest last) + the median of each stage, for the report."""
    try:
        with _lock:
            hist = [dict(r) for r in _hist]
        med: dict[str, int] = {}
        for k in (*STAGES, "total"):
            vals = sorted(int(r[k]) for r in hist)
            if vals:
                med[k] = vals[len(vals) // 2]
        return {"questions": hist, "median": med, "last": line(hist[-1]) if hist else ""}
    except Exception:
        return {"questions": [], "median": {}, "last": ""}


def reset() -> None:
    """Tests."""
    global _cur
    with _lock:
        _cur = None
        _hist.clear()
        _wake[0] = -1e9
