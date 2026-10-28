"""In-game comfort: what must NOT annoy the player (noise budget, danger flash, voice ducking, analysis rate).

Real feedback (Seraphine game, 2026-10-28, 40 min): 228 messages (5.6 / min: macro 91, roam 37, personal
danger 26, praise 20, objective 18, scoreboard 17), a thick red frame around the whole screen while the
shop was open, the coach talking all the time. Every rule here is pure, thread-safe and never raises.

* :class:`NoiseBudget` - the LAST gate before a message is written (HUD line / toast) or spoken, after
  every other policy (``voice_policy``, ``presenter``, the throttler):

  - DANGER (level 2, a gank warning, my base attacked) is never capped; the same danger text is not
    repeated within :data:`DANGER_REPEAT_S`;
  - quiet moments (:class:`Quiet`: loading, dead, in base, shop open, fight) -> only DANGER (and the
    death lesson / what the player asked);
  - objective timers only for an objective my role plays or that I stand next to
    (``voice_policy.objective_involved``);
  - praise at most once per :data:`PRAISE_GAP_S`, scoreboard insights once per :data:`SCOREBOARD_GAP_S`;
  - never the same text twice within :data:`REPEAT_S` (written or spoken);
  - per skill level, at most :data:`LEVEL_CAPS` written / spoken lines per rolling minute outside danger.

* :class:`FlashPulse` - the red screen-edge flash: ``subtle`` (default) = a thin frame, one soft pulse of
  :data:`SUBTLE_PULSE_S` at :data:`SUBTLE_PEAK` opacity; ``full`` = the old bright frame, fading over
  :data:`FULL_PULSE_S` (never longer than 2 s); one pulse per danger episode (:data:`FLASH_REARM_S`);
  never while dead, in base, the shop open or in a fight (the banner says it).

* :func:`ducked_volume` - less urgent spoken lines are quieter than danger (``cfg.voice_ducking``).

* :func:`low_value_reason` - when the minimap analysis cannot change anything for the player (dead,
  in base with the shop open, loading screen), the engine analyses at :data:`LOW_VALUE_FPS`.
"""

from __future__ import annotations

import logging
import math
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------- noise budget
#: per skill level: (written lines, spoken lines) per rolling minute, danger excluded
LEVEL_CAPS: dict[str, tuple[int, int]] = {
    "debutant": (3, 2), "intermediaire": (3, 2), "avance": (2, 1), "expert": (1, 1)}
WINDOW_S = 60.0
REPEAT_S = 60.0                # the same text (normalised) is never written / said twice within this
DANGER_REPEAT_S = 20.0         # ... a danger text: a new gank 20 s later is a new gank
PRAISE_GAP_S = 300.0           # one praise per 5 minutes
SCOREBOARD_GAP_S = 180.0       # one scoreboard insight per 3 minutes
LOADING_GT_S = 15.0            # game clock below this: loading screen / fountain start

_DANGER = 2
_WARNING = 1
_GANK_KINDS = ("jungler_approach", "roam_approach", "collapse")
#: never budgeted: the player asked (F9) / the death lesson (the one thing shown while dead)
_EXEMPT_KINDS = ("jungler_where", "death_recap")


@dataclass(frozen=True)
class Quiet:
    """What the player is busy with right now (any True = only DANGER gets through)."""

    loading: bool = False
    dead: bool = False
    in_base: bool = False
    shop_open: bool = False
    fight: bool = False

    def reason(self) -> str | None:
        for name in ("loading", "dead", "shop_open", "in_base", "fight"):
            if getattr(self, name):
                return name
        return None


def _kind(alert: Any) -> str:
    k = getattr(alert, "kind", None)
    return str(getattr(k, "value", k) or "")


def _level(alert: Any) -> int:
    try:
        return int(getattr(alert, "level", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _norm(text: Any) -> str:
    return " ".join(str(text or "").casefold().split())


def is_danger(alert: Any) -> bool:
    """A message the player must get whatever the budget: level DANGER, a gank warning (the early
    "X vient vers toi" is the point of the app), my base attacked."""
    lvl = _level(alert)
    key = str(getattr(alert, "key", "") or "")
    return lvl >= _DANGER or (_kind(alert) in _GANK_KINDS and lvl >= _WARNING) \
        or key == "personal_danger:siege"


def normalize_level(level: Any) -> str:
    s = str(level or "").strip().casefold()
    return s if s in LEVEL_CAPS else "intermediaire"


class NoiseBudget:
    """THE last gate on what reaches the player. ``allow`` checks AND records (one call per
    message actually written / spoken). Thread-safe, never raises (allows on error)."""

    def __init__(self, level: Any = "intermediaire") -> None:
        self._lock = threading.Lock()
        self.level = normalize_level(level)
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._sent: dict[str, deque] = {"text": deque(), "voice": deque()}
            self._texts: dict[str, float] = {}
            self._last_kind: dict[str, float] = {}
            self.stats: dict[str, int] = {}

    def set_level(self, level: Any) -> None:
        self.level = normalize_level(level)

    def _count(self, what: str) -> None:
        self.stats[what] = self.stats.get(what, 0) + 1

    def allow(self, alert: Any, t: float, channel: str = "text", quiet: Quiet | None = None, *,
              role: Any = None, me_uv: Any = None, gt: float | None = None) -> bool:
        try:
            with self._lock:
                why = self._why_not(alert, float(t), channel, quiet, role, me_uv, gt)
                if why is not None:
                    self._count("drop:" + why)
                    log.debug("Noise budget: %s dropped (%s): %s", channel, why, getattr(alert, "text", ""))
                    return False
                self._remember(alert, float(t), channel)
                self._count("pass:" + channel)
                return True
        except Exception:
            log.debug("NoiseBudget.allow failed", exc_info=True)
            return True

    def _why_not(self, alert: Any, now: float, channel: str, quiet: Quiet | None, role: Any, me_uv: Any,
                 gt: float | None) -> str | None:
        if not math.isfinite(now):
            return None
        kind = _kind(alert)
        if kind in _EXEMPT_KINDS:
            return None
        text = _norm(getattr(alert, "text", ""))
        seen = self._texts.get(text)
        if is_danger(alert):
            if seen is not None and 0.0 <= now - seen < DANGER_REPEAT_S:
                return "repeat"
            return None
        q = quiet.reason() if quiet is not None else None
        if q is not None:
            return "quiet_" + q
        if kind == "objective_soon":
            try:
                from treeaicoach.voice_policy import objective_involved

                if not objective_involved(str(getattr(alert, "key", "") or ""), role, me_uv, gt):
                    return "objective_not_mine"
            except Exception:
                pass
        gap = PRAISE_GAP_S if kind == "praise" else SCOREBOARD_GAP_S if kind == "scoreboard" else None
        last = self._last_kind.get(kind)
        if gap is not None and last is not None and 0.0 <= now - last < gap:
            return kind + "_gap"
        if seen is not None and 0.0 <= now - seen < REPEAT_S:
            return "repeat"
        if kind == "objective_soon":
            return None                      # my objective (checked above): rare, timed, never starved
        sent = self._sent.get(channel if channel in self._sent else "text")
        while sent and now - sent[0] >= WINDOW_S:
            sent.popleft()
        cap = LEVEL_CAPS[self.level][1 if channel == "voice" else 0]
        if len(sent) >= cap:
            return "cap_" + channel
        return None

    def _remember(self, alert: Any, now: float, channel: str) -> None:
        self._texts[_norm(getattr(alert, "text", ""))] = now
        if len(self._texts) > 300:
            for k in sorted(self._texts, key=self._texts.get)[:150]:
                self._texts.pop(k, None)
        if not is_danger(alert):
            self._last_kind[_kind(alert)] = now
            self._sent["voice" if channel == "voice" else "text"].append(now)


# ---------------------------------------------------------------------------------- danger flash
FLASH_STYLES = ("subtle", "full")
SUBTLE_PEAK = 0.55
SUBTLE_PULSE_S = 1.2
FULL_PULSE_S = 2.0
FLASH_REARM_S = 8.0            # one pulse per danger episode: a new one at most every 8 s


class FlashPulse:
    """The danger flash intensity (0..1) at ``now``. Not thread-safe by itself: one owner (the
    overlay state builder). Never raises."""

    def __init__(self) -> None:
        self._src: float | None = None      # the danger time that started / was skipped last
        self._start: float | None = None

    def intensity(self, now: float, last_danger: float | None, style: Any = "subtle",
                  suppressed: bool = False) -> float:
        try:
            now = float(now)
            subtle = str(style or "subtle") != "full"
            dur = SUBTLE_PULSE_S if subtle else FULL_PULSE_S
            if last_danger is not None and last_danger != self._src:
                self._src = float(last_danger)
                fresh = 0.0 <= now - self._src < dur
                if fresh and not suppressed and (self._start is None or now - self._start >= FLASH_REARM_S):
                    self._start = self._src
            if suppressed or self._start is None:
                return 0.0
            age = now - self._start
            if not 0.0 <= age < dur:
                return 0.0
            if subtle:
                return float(SUBTLE_PEAK * math.sin(math.pi * min(1.0, age / dur)) ** 0.5)
            return float(1.0 - age / dur)
        except Exception:
            return 0.0


def flash_thickness_scale(style: Any) -> float:
    """Frame thickness factor of the flash style (1.0 = the old 10 px at 1080p)."""
    return 0.4 if str(style or "subtle") != "full" else 1.0


# ---------------------------------------------------------------------------------- voice
#: a spoken line's volume factor by level (0 info, 1 warning, 2 danger) when ducking is on
DUCK_GAIN = {0: 0.7, 1: 0.85, 2: 1.0}


def ducked_volume(volume: Any, level: Any, ducking: bool = True) -> int:
    """The volume (0..100) to speak a line of ``level`` at. Never raises."""
    try:
        v = int(max(0, min(100, int(volume))))
        if not ducking:
            return v
        lvl = max(0, min(2, int(level)))
        return int(round(v * DUCK_GAIN[lvl]))
    except Exception:
        return 100


# ---------------------------------------------------------------------------------- analysis rate
LOW_VALUE_FPS = 2.0            # minimap analysis rate while it cannot matter


def low_value_reason(dead: bool = False, in_base: bool = False, shop_open: bool = False,
                     gt: float | None = None, threat: int = 0) -> str | None:
    """Why the minimap analysis can slow down now (None = it matters). A threat always matters."""
    try:
        if int(threat or 0) >= 1:
            return None
        if gt is not None and math.isfinite(float(gt)) and float(gt) < LOADING_GT_S:
            return "loading"
        if dead:
            return "dead"
        if in_base and shop_open:
            return "shop"
        return None
    except Exception:
        return None


STATIC_FPS = 2.5               # minimap analysis rate while the minimap does not change at all (game paused...)
STATIC_MAX_DIFF = 4            # max |diff| (0..255) between two 48 x 48 grey thumbnails = "nothing moved"
STATIC_FRAMES = 3              # ... for this many frames in a row


class SceneMotion:
    """Does the minimap move? A 48 x 48 grey thumbnail compared with the previous one (~0.1 ms).
    Any walking champion, ping, camera move or fog change breaks the streak. Never raises."""

    def __init__(self) -> None:
        self._prev: Any = None
        self.still = 0

    def update(self, frame: Any) -> bool:
        """Feed one frame; True when the scene has been static for :data:`STATIC_FRAMES` frames."""
        try:
            import cv2
            import numpy as np

            if frame is None or getattr(frame, "size", 0) == 0:
                self._prev, self.still = None, 0
                return False
            g = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            th = cv2.resize(g, (48, 48), interpolation=cv2.INTER_AREA)
            prev, self._prev = self._prev, th
            if prev is None or prev.shape != th.shape:
                self.still = 0
                return False
            d = int(np.max(cv2.absdiff(th, prev)))
            self.still = self.still + 1 if d <= STATIC_MAX_DIFF else 0
            return self.still >= STATIC_FRAMES
        except Exception:
            self._prev, self.still = None, 0
            return False

    def reset(self) -> None:
        self._prev, self.still = None, 0


def calm_period(base: float, low_value: str | None, static: bool) -> float:
    """The analysis period once the governor gave ``base`` (calm or burst): slower when the analysis
    cannot matter (:func:`low_value_reason`) or nothing moves. A burst (shorter base) is kept."""
    try:
        p = float(base)
        if low_value is not None:
            p = max(p, 1.0 / LOW_VALUE_FPS)
        elif static:
            p = max(p, 1.0 / STATIC_FPS)
        return p
    except Exception:
        return float(base)


# ---------------------------------------------------------------------------------- beeps
BEEP_SAME_S = 20.0             # the same threat beeps at most once per 20 s
BEEP_MIN_GAP_S = 5.0           # ... and any two beeps are at least 5 s apart (a NEW danger within 20 s)


def threat_id(alert: Any) -> str:
    """Who / what the danger is: the champion (the gank and the personal danger of the same Alistar are
    the same threat), else the alert key."""
    alias = getattr(alert, "alias", None)
    if alias:
        return "who:" + str(alias)
    return "key:" + str(getattr(alert, "key", "") or _kind(alert))


class BeepGate:
    """THE danger beep rule: only for a DANGER that is spoken / shown (called after the noise budget),
    never while dead / in base / shop open (except my base attacked), the same threat once per
    :data:`BEEP_SAME_S`, any two beeps :data:`BEEP_MIN_GAP_S` apart. Thread-safe, never raises."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._last_t = -math.inf
            self._by_threat: dict[str, float] = {}
            self.stats = {"beep": 0, "skip": 0}

    def allow(self, alert: Any, t: float, quiet: Quiet | None = None) -> bool:
        try:
            now = float(t)
            key = str(getattr(alert, "key", "") or "")
            siege = key.endswith(":siege")
            ok = _level(alert) >= _DANGER
            if ok and quiet is not None and not siege and (quiet.dead or quiet.in_base or quiet.shop_open
                                                           or quiet.loading):
                ok = False
            tid = threat_id(alert)
            with self._lock:
                if ok and 0.0 <= now - self._last_t < BEEP_MIN_GAP_S:
                    ok = False
                last = self._by_threat.get(tid)
                if ok and last is not None and 0.0 <= now - last < BEEP_SAME_S:
                    ok = False
                if ok:
                    self._last_t = now
                    self._by_threat[tid] = now
                self.stats["beep" if ok else "skip"] += 1
            return ok
        except Exception:
            return True


def engine_beep_allowed(eng: Any, alert: Any, t: float | None = None) -> bool:
    """:class:`BeepGate` of the engine (one per game, reset by :func:`engine_reset`)."""
    try:
        g = getattr(eng, "_beep_gate", None)
        if g is None:
            g = eng._beep_gate = BeepGate()
        if t is None:
            clock = getattr(eng, "_clock", None)
            import time as _time

            t = float(clock()) if callable(clock) else _time.monotonic()
        return g.allow(alert, t, engine_quiet(eng))
    except Exception:
        return True


# ---------------------------------------------------------------------------------- engine glue
def _finite(x: Any) -> float | None:
    try:
        f = float(x)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def engine_quiet(eng: Any, gt: float | None = None) -> Quiet:
    """The engine's :class:`Quiet` now (Live Client death, the tactics' base / fight state, the shop
    watcher of the overlay state). Never raises."""
    try:
        game = getattr(eng, "_game", None)
        me = getattr(game, "me", None)
        tac = getattr(eng, "_tactics", None)
        ctx = tac.speech_context() if tac is not None else None
        w = getattr(eng, "_shop_watcher", None)
        g = _finite(gt) if gt is not None else _finite(getattr(game, "game_time", None))
        return Quiet(loading=g is not None and g < LOADING_GT_S,
                     dead=bool(getattr(me, "is_dead", False)),
                     in_base=bool(getattr(ctx, "in_base", False)),
                     shop_open=bool(getattr(w, "shop_open", False)),
                     fight=bool(tac is not None and tac.in_fight()))
    except Exception:
        return Quiet()


def _engine_budget(eng: Any, attr: str = "_noise_budget") -> NoiseBudget:
    nb = getattr(eng, attr, None)
    level = getattr(getattr(eng, "_cfg", None), "skill_level", "intermediaire")
    if nb is None:
        nb = NoiseBudget(level)
        setattr(eng, attr, nb)
    else:
        nb.set_level(level)
    return nb


def engine_allow(eng: Any, alert: Any, t: float, gt: float | None, channel: str = "text") -> bool:
    """THE noise budget of the engine (one per game: :func:`engine_reset`) for ``alert`` written
    (``channel="text"``) or spoken (``"voice"``). Never raises (True on error)."""
    try:
        game = getattr(eng, "_game", None)
        tac = getattr(eng, "_tactics", None)
        ctx = tac.speech_context() if tac is not None else None
        role = getattr(getattr(game, "me", None), "position", None) or getattr(ctx, "role", None)
        return _engine_budget(eng).allow(alert, t, channel, engine_quiet(eng, gt), role=role,
                                         me_uv=getattr(ctx, "me_uv", None), gt=gt)
    except Exception:
        log.debug("engine_allow failed", exc_info=True)
        return True


def engine_reset(eng: Any) -> None:
    """New game: fresh budgets (messages and the Discord bot)."""
    for attr in ("_noise_budget", "_bot_noise_budget", "_beep_gate"):
        nb = getattr(eng, attr, None)
        if nb is not None:
            nb.reset()


#: the Discord bot's spoken calls under the same caps (per minute, repeats); ganks are danger
BOT_CAPPED = frozenset({"team_call", "objective", "team_brain", "coach", "jungler_seen", "summary"})
BOT_DANGER = frozenset({"gank", "ally_gank"})


class _BotCall:
    def __init__(self, kind: str, text: str, key: str) -> None:
        self.kind = "bot_" + kind
        self.level = _DANGER if kind in BOT_DANGER else 0
        self.text = text or key or kind
        self.key = key


def bot_allow(eng: Any, kind: str, text: str = "", info: dict | None = None, t: float | None = None) -> bool:
    """Same caps for the bot Discord's spoken calls (no quiet moments: it talks to the whole team).
    Never raises (True on error)."""
    try:
        if kind not in BOT_CAPPED and kind not in BOT_DANGER:
            return True
        if t is None:
            clock = getattr(eng, "_clock", None)
            import time as _time

            t = float(clock()) if callable(clock) else _time.monotonic()
        key = str((info or {}).get("key", "") or "") if isinstance(info, dict) else ""
        return _engine_budget(eng, "_bot_noise_budget").allow(_BotCall(kind, str(text or ""), key), t, "voice")
    except Exception:
        return True


__all__ = ["LEVEL_CAPS", "Quiet", "NoiseBudget", "is_danger", "FlashPulse", "FLASH_STYLES",
           "flash_thickness_scale", "ducked_volume", "DUCK_GAIN", "low_value_reason", "LOW_VALUE_FPS", "engine_quiet",
           "engine_allow", "engine_reset", "bot_allow", "SceneMotion", "calm_period",
           "BeepGate", "engine_beep_allowed", "threat_id"]
