"""Personal reminders built only from the player's OWN data (official Live Client Data API).

* ``RECALL_GOLD``: my current gold is at least ``cfg.recall_gold_threshold`` while I am out of
  my base -> "Tu as 1300 pièces d'or, pense à rentrer." At most every :data:`RECALL_REPEAT_S`;
  if I left the base already holding that much gold (saving for an item), I am only reminded
  after earning :data:`RECALL_MIN_GAIN` more. Never while dead or while my position is unknown.
  (Not talking over a gank is the engine's job: it drops these reminders when an enemy is near.)
* ``CONTROL_WARD``: I am in my base (or dead, i.e. able to shop), after 3:00, with at least
  75 gold, no control ward (itemID 2055) and a free item slot -> "Pense à acheter une balise de
  contrôle." Once per base visit (a visit ends after :data:`BASE_EXIT_DEBOUNCE_S` outside).

Nothing about other players is used. Summoner's Rift only.
"""

from __future__ import annotations

import logging
import math
import numbers
import threading
import time
from typing import Any

from treeaicoach import geometry
from treeaicoach.alerts import Alert, AlertKind, Level, phrase

log = logging.getLogger(__name__)

CONTROL_WARD_ID = 2055
CONTROL_WARD_COST = 75
#: Trinket-slot items (do not take one of the 6 item slots).
TRINKET_IDS: frozenset[int] = frozenset({3340, 3363, 3364, 3330, 3513})
ITEM_SLOTS = 6
DEFAULT_RECALL_GOLD = 1300
RECALL_REPEAT_S = 90.0           # min delay between two recall reminders (monotonic s)
RECALL_MIN_GAIN = 250            # left the base with >= threshold gold: wait for this much more
RECALL_MIN_GAME_TIME_S = 90.0
CONTROL_WARD_MIN_GAME_TIME_S = 180.0
BASE_DWELL_S = 1.0               # in base this long before the control ward reminder
BASE_EXIT_DEBOUNCE_S = 3.0       # out of base this long = the base visit is over
RESPAWN_IN_BASE_S = 8.0          # just respawned = at the fountain even if not detected yet
DEAD_SHOP_DELAY_S = 6.0          # dead players can shop; wait for the death recap first
GAME_TIME_BACK_RESET_S = 3.0

_RECALL_GOLD: Any = getattr(AlertKind, "RECALL_GOLD", "recall_gold")
_CONTROL_WARD: Any = getattr(AlertKind, "CONTROL_WARD", "control_ward")


def format_gold(gold: float) -> str:
    """``1450`` -> ``"1 450"`` (French thousands separator, non-breaking space)."""
    n = max(0, int(gold))
    return f"{n:,}".replace(",", " ")


def _finite(x: Any) -> float | None:
    """Finite float from a real number (numpy scalars included, bool excluded), else None."""
    if isinstance(x, bool) or not isinstance(x, numbers.Real):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _clean_pos(pos: Any) -> tuple[float, float] | None:
    """Valid minimap position ``(u, v)`` or None."""
    try:
        u, v = _finite(pos[0]), _finite(pos[1])  # type: ignore[index]
    except (TypeError, IndexError, KeyError):
        return None
    if u is None or v is None or not (-0.1 <= u <= 1.1 and -0.1 <= v <= 1.1):
        return None
    return u, v


def _own_base(pos: tuple[float, float] | None, team: str | None) -> bool | None:
    """True/False if ``pos`` is/isn't in my own base (None when the position is unknown)."""
    if pos is None:
        return None
    try:
        zone = geometry.classify_zone(pos[0], pos[1])
        if geometry.is_base(zone):
            owner = geometry.zone_owner(zone)
            return team is None or owner == team
        return bool(team is not None and geometry.in_fountain(pos[0], pos[1], team))
    except Exception:
        log.debug("Zone lookup failed for %r", pos, exc_info=True)
        return None


class PersonalReminders:
    """Recall / control ward reminders from my own gold and inventory. Thread-safe."""

    def __init__(self, cfg: Any) -> None:
        self._lock = threading.Lock()
        self._recall_on = True
        self._ward_on = True
        self._threshold = DEFAULT_RECALL_GOLD
        self._error_logged_at = -math.inf
        self.apply_config(cfg)
        self._clear()

    # -- public -------------------------------------------------------------------------

    def apply_config(self, cfg: Any) -> None:
        """Take new settings into account (``recall_reminder``, ``recall_gold_threshold``,
        ``control_ward_reminder``)."""
        try:
            recall = getattr(cfg, "recall_reminder", True)
            ward = getattr(cfg, "control_ward_reminder", True)
            thr = _finite(getattr(cfg, "recall_gold_threshold", DEFAULT_RECALL_GOLD))
            with self._lock:
                self._recall_on = recall if isinstance(recall, bool) else True
                self._ward_on = ward if isinstance(ward, bool) else True
                self._threshold = int(min(max(thr, 100), 20000)) if thr is not None else DEFAULT_RECALL_GOLD
        except Exception:
            log.exception("PersonalReminders.apply_config failed")

    def update(self, t: float, game: Any, me_pos: tuple[float, float] | None, in_base: bool) -> list[Alert]:
        """One analysis tick. ``me_pos`` = my minimap position (None if unknown), ``in_base`` =
        the engine thinks I am in my base. Returns 0 or 1 alert. Never raises."""
        try:
            with self._lock:
                return self._update_locked(t, game, me_pos, in_base)
        except Exception:
            now = time.monotonic()
            if now - self._error_logged_at > 60.0:
                self._error_logged_at = now
                log.exception("PersonalReminders.update failed")
            return []

    def hint(self) -> str | None:
        """Short HUD hint from the last tick, e.g. ``"1 450 PO — pense à rentrer"`` (or None)."""
        return self._hint

    def reset(self) -> None:
        """Forget everything (new game)."""
        with self._lock:
            self._clear()

    # -- internals ----------------------------------------------------------------------

    def _clear(self) -> None:
        self._in_base = False
        self._visit = 0
        self._base_since: float | None = None
        self._out_since: float | None = None
        self._ward_reminded_visit = -1
        self._gold_at_exit: float | None = None
        self._last_recall_t: float | None = None
        self._was_dead = False
        self._death_t: float | None = None
        self._respawn_t: float | None = None
        self._last_game_time: float | None = None
        self._hint: str | None = None

    def _update_locked(self, t: Any, game: Any, me_pos: Any, in_base: Any) -> list[Alert]:
        now = _finite(t)
        if now is None or game is None:
            return []
        me = getattr(game, "me", None)
        if me is None or not bool(getattr(game, "is_summoners_rift", False)):
            self._hint = None
            return []
        game_time = _finite(getattr(game, "game_time", None)) or 0.0
        last_gt = self._last_game_time
        if last_gt is not None and game_time < last_gt - GAME_TIME_BACK_RESET_S:
            log.info("Reminders: game clock went back, reset")
            self._clear()
        self._last_game_time = game_time if self._last_game_time is None else max(game_time, self._last_game_time)

        gold = _finite(getattr(game, "current_gold", None))
        if not gold:
            gold = _finite(getattr(me, "current_gold", None)) or 0.0
        items = [int(i) for i in (getattr(me, "items", None) or []) if isinstance(i, numbers.Integral)]
        dead = bool(getattr(me, "is_dead", False))
        team = getattr(me, "team", None)
        pos = _clean_pos(me_pos) if me_pos is not None else None

        # death / respawn (a respawn puts me on the fountain)
        if dead and not self._was_dead:
            self._death_t = now
        elif not dead and self._was_dead:
            self._respawn_t = now
        self._was_dead = dead

        own = _own_base(pos, team if team in ("ORDER", "CHAOS") else None)
        at_base = own is True or (bool(in_base) and own is not False)
        if not dead and self._respawn_t is not None and 0.0 <= now - self._respawn_t < RESPAWN_IN_BASE_S:
            at_base = at_base or own is not False or pos is None
        can_shop = at_base or (dead and self._death_t is not None and now - self._death_t >= DEAD_SHOP_DELAY_S)
        self._track_visit(now, can_shop, gold)

        alerts: list[Alert] = []
        ward_needed = (
            game_time >= CONTROL_WARD_MIN_GAME_TIME_S
            and gold >= CONTROL_WARD_COST
            and CONTROL_WARD_ID not in items
            and sum(1 for i in items if i not in TRINKET_IDS) < ITEM_SLOTS
        )
        if (self._ward_on and ward_needed and can_shop and self._in_base and self._base_since is not None
                and now - self._base_since >= BASE_DWELL_S and self._ward_reminded_visit != self._visit):
            self._ward_reminded_visit = self._visit
            alerts.append(Alert(kind=_CONTROL_WARD, level=Level.INFO,
                                text=phrase(_CONTROL_WARD, Level.INFO, None), key="control_ward", t=now))

        recall_now = (
            not dead
            and pos is not None
            and not at_base
            and not self._in_base
            and game_time >= RECALL_MIN_GAME_TIME_S
            and gold >= self._threshold
        )
        gained = (self._gold_at_exit is None or self._gold_at_exit < self._threshold
                  or gold >= self._gold_at_exit + RECALL_MIN_GAIN)
        if (self._recall_on and recall_now and gained and not alerts
                and (self._last_recall_t is None or not 0.0 <= now - self._last_recall_t < RECALL_REPEAT_S)):
            self._last_recall_t = now
            alerts.append(Alert(kind=_RECALL_GOLD, level=Level.INFO,
                                text=phrase(_RECALL_GOLD, Level.INFO, None, None, int(gold)),
                                key="recall_gold", t=now))

        if self._recall_on and recall_now:
            self._hint = f"{format_gold(gold)} PO — pense à rentrer"
        elif self._ward_on and ward_needed and can_shop:
            self._hint = "Pense à la balise de contrôle"
        else:
            self._hint = None
        return alerts

    def _track_visit(self, now: float, can_shop: bool, gold: float) -> None:
        """Debounced base visits: a visit starts on arrival, ends after a few seconds outside."""
        if can_shop:
            self._out_since = None
            if not self._in_base:
                self._in_base = True
                self._visit += 1
                self._base_since = now
            return
        if not self._in_base:
            return
        if self._out_since is None:
            self._out_since = now
        elif now - self._out_since >= BASE_EXIT_DEBOUNCE_S or now < self._out_since:
            self._in_base = False
            self._base_since = None
            self._out_since = None
            self._gold_at_exit = gold
