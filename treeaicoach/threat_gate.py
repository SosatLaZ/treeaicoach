"""ONE THREAT = ONE ALERT: the last gate of the gank / danger alerts before they are said or written.

Real games (``tools/gank_alerts_truth.py``: 28 games with LCU truth + 22 records without, the
3.1.0 ones included): 67 gank-type alerts per game, half of them repeats of the same enemies
within 10 s ("Trundle arrive, recule !" x3, "Recule vers ta tour : Alistar sur toi" + "Recule !"
on the same tick, a collapse then a roam on the same two enemies...), and the early warnings
("X vient vers toi par la rivière", WARNING) right only 1 time in 2 (the enemy walking parallel in
the river, a mid laner going to the other side, a jungler turning to his camp). The analysers
(gank.py, danger.py) each keep their own cooldown; nothing merged them. This gate does:

* an **episode** per enemy, across kinds: an alert naming enemies that had an alert in the last
  :data:`EPISODE_S` seconds passes only when it ESCALATES (higher level, or the enemy is
  :data:`CLOSER_BY` nearer than when last announced, or a DANGER still going on
  :data:`REANNOUNCE_S` after it was said); a dropped alert extends the episode;
* the same tick: highest level first (a "Recule !" swallows the written warning of the same enemy);
* an anonymous alert ("Gank ! Un ennemi arrive") merges into any named episode of the last
  :data:`ANON_MERGE_S` seconds;
* ``laner_mia`` ("X a disparu"): once per enemy per :data:`MIA_COOLDOWN_S`, never right after he
  was announced coming;
* an early warning about an enemy farther than :data:`HOLD_FROM` is HELD: said as soon as he
  keeps closing in (:data:`HOLD_CLOSER` nearer than at the offer, or within :data:`HOLD_NEAR`),
  checked from :data:`HOLD_S` to :data:`HOLD_MAX_S` after it; dropped otherwise (a jungler that
  went back into the fog on his way: said). Measured: confirmed after 2.5 s = precision 0.66,
  not confirmed = 0.32.

Pure (no I/O, no clock); never raises. The engine (``engine_vision._gate_threats``) and
``tools/gank_alerts_truth.py --gate`` run the same code (numbers: docs/ARCHITECTURE.md §17).
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

#: kinds handled here (values of alerts.AlertKind)
GATED_KINDS = frozenset({"roam_approach", "jungler_approach", "collapse", "personal_danger", "laner_mia"})
DANGER_KINDS = frozenset({"roam_approach", "jungler_approach", "collapse", "personal_danger"})
EARLY_KINDS = frozenset({"roam_approach", "jungler_approach"})
EPISODE_S = 10.0
CLOSER_BY = 0.04
#: a DANGER alert still going on is said again after this long (the threat is still there)
REANNOUNCE_S = 15.0
REANNOUNCE_LEVEL = 2
ANON_MERGE_S = 10.0
MIA_COOLDOWN_S = 300.0
MIA_AFTER_EPISODE_S = 20.0
#: an early warning (WARNING "X vient vers toi") is held HOLD_S seconds and said only if X kept
#: closing in (HOLD_CLOSER nearer, or already within HOLD_NEAR): measured on the 28 truth games,
#: confirmed after 2.5 s = precision 0.66, not confirmed = 0.32 (enemy walking parallel in the
#: river, a mid laner going to the other side, a jungler turning to his camp)
HOLD_S = 2.5
#: ... and watched until this long after the offer (said as soon as he does close in)
HOLD_MAX_S = 5.0
#: an early warning about an enemy announced in the last L1_MEMORY_S seconds: not again
L1_MEMORY_S = 10.0
HOLD_CLOSER = 0.015
HOLD_NEAR = 0.10
#: only the early warnings about an enemy at least this far are held (nearer: said at once)
HOLD_FROM = 0.10
#: kinds said anyway when the enemy went back into the fog during the hold (a jungler
#: vanishing on his way to me is a gank path); the others are dropped
HOLD_GONE_SAYS: frozenset = frozenset({"jungler_approach"})
ANON = "*"
#: danger.py: the written reason that goes with a spoken "Recule !" (same tick)
COMPANION_KEY_SUFFIX = "recule:why"


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class ThreatGate:
    """Per-game state. :meth:`offer` decides one alert (said now / dropped / held),
    :meth:`due` releases the held early warnings that the next ticks confirmed."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        #: member -> (last time, last level, distance then)
        self._ep: dict[str, tuple[float, int, float | None]] = {}
        #: member -> time it was last ANNOUNCED (the episode above is extended by the dropped ones)
        self._said: dict[str, float] = {}
        self._mia: dict[str, float] = {}
        #: held early warnings: [due time, item, kind, level, members, distance at offer]
        self._held: list[list[Any]] = []
        self.dropped = 0
        self.passed = 0
        self.held = 0

    # ------------------------------------------------------------------ public
    def offer(self, t: float, item: Any, kind: str, level: int, members: Iterable[str] = (),
              dist: float | None = None, companion: bool = False) -> bool:
        """True = say / write ``item`` now; False = dropped, or held for :meth:`due`.
        ``members``: the enemy aliases it names (empty = anonymous); ``dist``: the nearest of
        them to me now (None unknown). ``companion``: the written reason of a spoken "Recule !"
        (danger.py ``recule:why``): passes with it, on the same tick only. Never raises."""
        try:
            t = float(t)
            kind = str(getattr(kind, "value", kind))
            level = int(level)
            mem = tuple(str(m) for m in members if m)
            d = _f(dist)
            if companion:
                ok = any(abs(self._said.get(m, -1e9) - t) < 1e-6 for m in (mem or (ANON,)))
                self.passed += ok
                self.dropped += not ok
                return ok
            if kind in EARLY_KINDS and level == 1 and HOLD_S > 0 and d is not None and d > HOLD_FROM:
                if not self._check(t, kind, level, mem, d, commit=False):
                    self.dropped += 1
                    return False
                if not any(h[4] == mem for h in self._held):
                    self._held.append([t + HOLD_S, item, kind, level, mem, d, t])
                    self.held += 1
                return False
            ok = self._check(t, kind, level, mem, d, commit=True)
        except Exception:
            log.debug("ThreatGate.offer failed", exc_info=True)
            return True
        if ok:
            self.passed += 1
        else:
            self.dropped += 1
        return ok

    def allow(self, t: float, kind: str, level: int, members: Iterable[str] = (),
              dist: float | None = None) -> bool:
        """Decide without holding (said now or dropped). Never raises."""
        try:
            return self._check(float(t), str(getattr(kind, "value", kind)), int(level),
                               tuple(str(m) for m in members if m), _f(dist), commit=True)
        except Exception:
            return True

    def due(self, t: float, dist_of: Callable[[str], float | None]) -> list[Any]:
        """The held early warnings to say now: their enemy kept closing in during the hold.
        ``dist_of(alias)`` = his distance to me now, None if not visible. Never raises."""
        out: list[Any] = []
        try:
            t = float(t)
            keep = []
            for h in self._held:
                due_t, item, kind, level, mem, d0, t0 = h
                if t < due_t:
                    keep.append(h)
                    continue
                if t > t0 + HOLD_MAX_S + 2.0:     # (no tick for a while: stale)
                    self.dropped += 1
                    continue
                ds = [d for d in (_f(dist_of(m)) for m in mem) if d is not None] if mem else []
                d1 = min(ds) if ds else None
                if d1 is None:
                    confirmed = kind in HOLD_GONE_SAYS
                else:
                    confirmed = d1 < d0 - HOLD_CLOSER or d1 <= HOLD_NEAR
                if confirmed:
                    if self._check(t, kind, level, mem, d1, commit=True):
                        self.passed += 1
                        out.append(item)
                    else:
                        self.dropped += 1
                elif t < t0 + HOLD_MAX_S:
                    keep.append(h)               # still watching him
                else:
                    self.dropped += 1
            self._held = [h for h in keep if h[0] > -1e9]
        except Exception:
            log.debug("ThreatGate.due failed", exc_info=True)
            self._held = []
        return out

    # ------------------------------------------------------------------ rules
    def _check(self, t: float, kind: str, level: int, members: tuple[str, ...], dist: float | None,
               commit: bool) -> bool:
        if kind not in GATED_KINDS:
            return True
        keys = members or (ANON,)
        if kind == "laner_mia":
            for m in keys:
                if t - self._mia.get(m, -1e9) < MIA_COOLDOWN_S:
                    return False
                ep = self._ep.get(m)
                if ep is not None and t - ep[0] < MIA_AFTER_EPISODE_S:
                    return False
            if commit:
                for m in keys:
                    self._mia[m] = t
            return True
        prev = [self._ep[m] for m in keys if m in self._ep and t - self._ep[m][0] < EPISODE_S]
        if not members:      # anonymous: any named episode of the last seconds is the same threat
            prev += [e for k, e in self._ep.items() if k != ANON and t - e[0] < ANON_MERGE_S]
        if kind in EARLY_KINDS and level == 1 and any(
                t - self._said[m] < L1_MEMORY_S for m in keys if m in self._said):
            return False     # announced already: an early warning never repeats it
        ok = True
        if prev:
            last = max(prev, key=lambda e: e[0])
            said_ago = min((t - self._said.get(m, -1e9) for m in keys), default=1e9)
            ok = (level > last[1]
                  or (dist is not None and last[2] is not None and dist < last[2] - CLOSER_BY)
                  or (level >= REANNOUNCE_LEVEL and said_ago >= REANNOUNCE_S))
        if not commit:
            return ok
        if ok:
            for m in keys:
                self._ep[m] = (t, level, dist)
                self._said[m] = t
            for h in self._held:     # a stronger alert said it: the held early warning is moot
                if set(h[4]) & set(keys) and level >= h[3]:
                    h[0] = -1e9
            self._held = [h for h in self._held if h[0] > -1e9]
        else:
            for m in keys:      # the threat goes on: the episode is extended, not re-announced
                e = self._ep.get(m)
                if e is not None:
                    self._ep[m] = (t, max(e[1], level), e[2])
        return ok

    def filter_tick(self, t: float, alerts: list, members_dist: Callable[[Any], tuple],
                    dist_of: Callable[[str], float | None]) -> list:
        """One tick: the alerts to say / write now = the held early warnings confirmed now +
        this tick's alerts that pass (highest level decided first, order kept).
        ``members_dist(alert) -> (members, distance)``; ``dist_of(alias)`` as :meth:`due`."""
        try:
            out = list(self.due(t, dist_of))
            order = sorted(range(len(alerts)), key=lambda i: -int(getattr(alerts[i], "level", 0) or 0))
            keep = set()
            for i in order:
                a = alerts[i]
                kind = str(getattr(a.kind, "value", a.kind))
                if kind not in GATED_KINDS:
                    keep.add(i)
                    continue
                members, dist = members_dist(a)
                comp = str(getattr(a, "key", "")).endswith(COMPANION_KEY_SUFFIX)
                if self.offer(t, a, kind, int(a.level), members, dist, companion=comp):
                    keep.add(i)
            return out + [a for i, a in enumerate(alerts) if i in keep]
        except Exception:
            log.debug("ThreatGate.filter_tick failed", exc_info=True)
            return list(alerts)
