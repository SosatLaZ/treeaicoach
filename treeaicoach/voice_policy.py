"""What is SPOKEN and what is only WRITTEN (HUD tip line + toasts), plus the anti-spam gate.

The user wants a quiet coach: by default (``cfg.voice_level == "minimal"``) the voice only says

* gank alerts (jungler / roam approach, collapse: WARNING and DANGER) and the F9 answer;
* the objective warnings 60 s before a spawn (``objective_soon:<kind>:<lead>`` with lead >= 45);
* stance changes (``stance:<level>`` keys, :class:`treeaicoach.coach.StanceAdvisor` already
  limits them to one every 2 min);
* praise for the big moments only (multikill, shutdown, solo kill, steal).

Everything else (macro tips, Tab insights, buy / recall / ward reminders, small praise, death
recap, jungler sightings...) is written in the HUD and as a toast. ``"normal"`` also speaks the
objective timers, macro tips, death recap, sightings, Tab insights and every praise (reminders
stay written); ``"bavard"`` speaks everything.

:class:`MessageGate` is the per-game anti-spam memory applied to every non-gank message,
spoken or written: a minimum interval per kind (e.g. one recall reminder every 150 s) and no
identical key / text again within a per-kind window. Gank alerts are never gated here (the
:class:`treeaicoach.alerts.AlertThrottler` handles them).

Pure Python, thread-safe, never raises from its public methods.
"""

from __future__ import annotations

import logging
import math
import threading
from typing import Any

from treeaicoach.alerts import AlertKind

log = logging.getLogger(__name__)

VOICE_LEVELS: tuple[str, ...] = ("minimal", "normal", "bavard")
DEFAULT_VOICE_LEVEL = "minimal"
GANK_KINDS = frozenset({AlertKind.JUNGLER_APPROACH, AlertKind.ROAM_APPROACH, AlertKind.COLLAPSE})
#: Always spoken (answers to a hotkey the player pressed).
ALWAYS_VOICE = frozenset({AlertKind.JUNGLER_WHERE})
#: Praise keys (praise.PraiseCoach) of the big moments, spoken even in "minimal".
BIG_PRAISE_PREFIXES: tuple[str, ...] = ("multi:", "shutdown:", "solo:", "steal:")
STANCE_PREFIX = "stance:"
#: Objective warnings at least this many seconds before the spawn are spoken in "minimal".
OBJECTIVE_VOICE_MIN_LEAD_S = 45
#: Spoken in "normal" (besides the "minimal" set).
NORMAL_VOICE = frozenset({AlertKind.OBJECTIVE_SOON, AlertKind.MACRO_TIP, AlertKind.DEATH_RECAP,
                          AlertKind.JUNGLER_SPOTTED, AlertKind.LANER_MIA, AlertKind.SCOREBOARD,
                          AlertKind.PRAISE})

#: Minimum interval between two messages of one kind (spoken or written), seconds.
KIND_GAP_S: dict[str, float] = {
    "macro_tip": 20.0, "praise": 12.0, "scoreboard": 30.0, "recall_gold": 150.0, "control_ward": 300.0,
    "objective_soon": 6.0, "jungler_spotted": 30.0, "laner_mia": 30.0, "death_recap": 0.0, "stance": 120.0,
}
#: The same key or the same text is not repeated within this window (per game), seconds.
DEDUPE_S: dict[str, float] = {
    "macro_tip": 240.0, "praise": 600.0, "scoreboard": 600.0, "recall_gold": 180.0, "control_ward": 600.0,
    "objective_soon": 200.0, "jungler_spotted": 60.0, "laner_mia": 90.0, "death_recap": 30.0, "stance": 120.0,
}
DEFAULT_GAP_S = 15.0
DEFAULT_DEDUPE_S = 180.0

#: Toast (kind, title) of the written-only messages, by alert kind.
TEXT_TOAST: dict[str, tuple[str, str]] = {
    "macro_tip": ("insight", "CONSEIL"), "recall_gold": ("insight", "RETOUR EN BASE"),
    "control_ward": ("insight", "BALISE"), "objective_soon": ("warning", "OBJECTIF"),
    "death_recap": ("danger", "TA MORT"), "jungler_spotted": ("warning", "JUNGLER"),
    "laner_mia": ("warning", "MIA"),
}


def normalize_level(value: Any) -> str:
    v = str(value or "").strip().lower()
    return v if v in VOICE_LEVELS else DEFAULT_VOICE_LEVEL


def kind_name(alert: Any) -> str:
    """Stable kind id of an alert (``"stance"`` for stance announcements)."""
    key = str(getattr(alert, "key", "") or "")
    if key.startswith(STANCE_PREFIX):
        return "stance"
    k = getattr(alert, "kind", "")
    return str(getattr(k, "value", k) or "")


def _objective_lead(key: str) -> float | None:
    try:
        return float(key.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def is_big_praise(alert: Any) -> bool:
    return str(getattr(alert, "key", "") or "").startswith(BIG_PRAISE_PREFIXES)


def route(alert: Any, voice_level: Any = DEFAULT_VOICE_LEVEL) -> str:
    """``"voice"`` (spoken, through the throttler) or ``"text"`` (HUD line + toast). Never raises."""
    try:
        kind = getattr(alert, "kind", None)
        if kind in GANK_KINDS or kind in ALWAYS_VOICE:
            return "voice"
        level = normalize_level(voice_level)
        if level == "bavard":
            return "voice"
        key = str(getattr(alert, "key", "") or "")
        if key.startswith(STANCE_PREFIX):
            return "voice"
        if kind == AlertKind.OBJECTIVE_SOON:
            lead = _objective_lead(key)
            if level == "normal" or (lead is not None and lead >= OBJECTIVE_VOICE_MIN_LEAD_S):
                return "voice"
            return "text"
        if kind == AlertKind.PRAISE and is_big_praise(alert):
            return "voice"
        if level == "normal" and kind in NORMAL_VOICE:
            return "voice"
        return "text"
    except Exception:
        log.debug("voice_policy.route failed", exc_info=True)
        return "text"


class MessageGate:
    """Per-game anti-spam memory (see the module docstring). Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._kind_t: dict[str, float] = {}
            self._seen: dict[str, float] = {}          # "k:<key>" / "x:<text>" -> time

    @staticmethod
    def _exempt(alert: Any) -> bool:
        return getattr(alert, "kind", None) in GANK_KINDS or getattr(alert, "kind", None) in ALWAYS_VOICE

    def check(self, alert: Any, t: float) -> bool:
        """True when ``alert`` may be said / shown now (nothing is remembered). Never raises."""
        try:
            if self._exempt(alert):
                return True
            with self._lock:
                return self._ok(alert, float(t))
        except Exception:
            log.debug("MessageGate.check failed", exc_info=True)
            return True

    def record(self, alert: Any, t: float) -> None:
        """Remember that ``alert`` was said / shown at ``t``. Never raises."""
        try:
            if self._exempt(alert):
                return
            kind = kind_name(alert)
            with self._lock:
                now = float(t)
                self._kind_t[kind] = now
                self._seen["k:" + str(getattr(alert, "key", "") or "")] = now
                self._seen["x:" + _norm(getattr(alert, "text", ""))] = now
                if len(self._seen) > 400:
                    for k in sorted(self._seen, key=self._seen.get)[:200]:
                        self._seen.pop(k, None)
        except Exception:
            log.debug("MessageGate.record failed", exc_info=True)

    def allow(self, alert: Any, t: float) -> bool:
        """:meth:`check` + :meth:`record` in one step (written messages)."""
        if self._exempt(alert):
            return True
        with self._lock:
            ok = self._ok(alert, float(t))
        if ok:
            self.record(alert, t)
        return ok

    def _ok(self, alert: Any, now: float) -> bool:
        if not math.isfinite(now):
            return False
        kind = kind_name(alert)
        last = self._kind_t.get(kind)
        if last is not None and 0.0 <= now - last < KIND_GAP_S.get(kind, DEFAULT_GAP_S):
            return False
        window = DEDUPE_S.get(kind, DEFAULT_DEDUPE_S)
        for k in ("k:" + str(getattr(alert, "key", "") or ""), "x:" + _norm(getattr(alert, "text", ""))):
            seen = self._seen.get(k)
            if seen is not None and 0.0 <= now - seen < window:
                return False
        return True


def _norm(text: Any) -> str:
    return " ".join(str(text or "").casefold().split())


__all__ = ["VOICE_LEVELS", "DEFAULT_VOICE_LEVEL", "route", "MessageGate", "TEXT_TOAST", "is_big_praise",
           "kind_name", "normalize_level"]
