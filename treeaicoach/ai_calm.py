"""When the AI may be asked AUTOMATICALLY: only in calm moments (user request, 2026-10-22).

Calm = I am dead (nothing to do but read), or the few seconds right after I got a kill with no
enemy seen near me on the minimap (tracker). Any other key moment of
:class:`treeaicoach.ai_advisor.AIAdvisor` is treated as "busy": deferred (its ``DEFER_S``) and
asked only if a calm moment comes in time, else dropped. The manual ask ("Hey coach" / F8) never
goes through this gate. Pure; never raises.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

KILL_CALM_S = 10.0          # after my kill, the calm window lasts this long ...
NEAR_R = 0.18               # ... while no enemy is seen within this minimap radius of me
SEEN_S = 3.0                # an enemy track counts as "near" when seen this recently


def _kills(game: Any) -> int | None:
    me = getattr(game, "me", None)
    sc = getattr(me, "scores", None)
    try:
        return int((sc or {}).get("kills")) if isinstance(sc, dict) and sc.get("kills") is not None else None
    except Exception:
        return None


def enemy_near(me_uv: Any, tracks: Iterable[Any], now: float, r: float = NEAR_R) -> bool:
    """An enemy track seen in the last :data:`SEEN_S` s within ``r`` of me (unknown position = near)."""
    if me_uv is None:
        return True
    try:
        mu, mv = float(me_uv[0]), float(me_uv[1])
        for k in tracks or ():
            if getattr(k, "relation", None) != "enemy":
                continue
            last = getattr(k, "last_seen", None)
            if not getattr(k, "visible", False) and (last is None or now - float(last) > SEEN_S):
                continue
            pos = k.position() if callable(getattr(k, "position", None)) else getattr(k, "uv", None)
            if pos is None:
                continue
            if math.hypot(float(pos[0]) - mu, float(pos[1]) - mv) <= r:
                return True
    except Exception:
        return True
    return False


class CalmGate:
    """Feed once per tick; :meth:`update` returns True when an automatic AI request may go."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._kills: int | None = None
        self._kill_t: float | None = None

    def update(self, t: float, game: Any, me_uv: Any = None, tracks: Iterable[Any] = (),
               threat: int = 0, in_fight: bool = False) -> bool:
        try:
            k = _kills(game)
            if k is not None:
                if self._kills is not None and k > self._kills:
                    self._kill_t = t
                elif self._kills is not None and k < self._kills:      # new game
                    self._kill_t = None
                self._kills = k
            if bool(getattr(getattr(game, "me", None), "is_dead", False)):
                return True
            if self._kill_t is None or not (0.0 <= t - self._kill_t <= KILL_CALM_S):
                return False
            return threat <= 0 and not in_fight and not enemy_near(me_uv, tracks, t)
        except Exception:
            return False
