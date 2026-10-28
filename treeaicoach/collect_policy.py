"""Which minimap frames are worth keeping for training ("Collecter des captures de minimap").

Before 2.x the engine saved a PNG every 30 s + a full JPEG every 4th one (199 files / 28 Mo for ~5
games), mostly calm frames the detector already gets right. Now a crop is saved only when it teaches
something, at most once every :data:`MIN_GAP_S` and :data:`MAX_FILES` / :data:`MAX_BYTES` per game:

* ``roster``  - a champion shown who is not in the game, or shown while the Live Client says he is dead
  (detector / roster disagreement);
* ``lowconf`` - an icon found but not named, or named with a low identity score;
* ``stack``   - two icons stacked on each other;
* ``fight``   - two allies and two enemies close together (team fight).

Crops are JPEG q80 (~35 Ko for a 383 px minimap). The full game window is saved once per game, when the shop is open.
Pure functions; never raise.
"""

from __future__ import annotations

from typing import Any, Iterable

MIN_GAP_S = 180.0          # at most one crop every 3 min of game
MAX_FILES = 25             # per game (crops + the shop screenshot)
MAX_BYTES = 4 * 1024 * 1024
JPEG_Q = 80
FULL_MAX_W = 1280          # the shop screenshot is downscaled to this width
LOW_ID = 0.5               # identity score below this = low confidence
FIGHT_R = 0.15             # minimap units around the fight centre
#: reasons by priority (the first one found names the file)
REASONS = ("roster", "lowconf", "stack", "fight")


def _f(x: Any) -> float | None:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def frame_reason(identified: Iterable[Any], tracks: Iterable[Any] = (), roster: Iterable[str] = (),
                 dead: Iterable[str] = ()) -> str | None:
    """The reason to keep this frame (see the module doc), or None for a frame without interest."""
    try:
        roster_s, dead_s = {str(a) for a in roster if a}, {str(a) for a in dead if a}
        found: set[str] = set()
        pts: list[tuple[str, float, float]] = []
        for x in identified or ():
            det = getattr(x, "det", x)
            alias, rel = getattr(x, "alias", None), str(getattr(x, "relation", "") or "")
            ids = _f(getattr(x, "id_score", None))
            if alias is None or (ids is not None and ids < LOW_ID):
                found.add("lowconf")
            elif roster_s and (alias not in roster_s or alias in dead_s):
                found.add("roster")
            u, v = _f(getattr(det, "u", None)), _f(getattr(det, "v", None))
            if u is not None and v is not None and rel:
                pts.append((rel, u, v))
        for k in tracks or ():
            if getattr(k, "visible", False) and getattr(k, "stacked_with", None) is not None:
                found.add("stack")
                break
        allies = [(u, v) for r, u, v in pts if r in ("ally", "self")]
        enemies = [(u, v) for r, u, v in pts if r == "enemy"]
        if len(allies) >= 2 and len(enemies) >= 2:
            for cu, cv in enemies:
                near_a = sum(1 for u, v in allies if (u - cu) ** 2 + (v - cv) ** 2 <= FIGHT_R ** 2)
                near_e = sum(1 for u, v in enemies if (u - cu) ** 2 + (v - cv) ** 2 <= FIGHT_R ** 2)
                if near_a >= 2 and near_e >= 2:
                    found.add("fight")
                    break
        for r in REASONS:
            if r in found:
                return r
    except Exception:
        return None
    return None


class GameBudget:
    """Per-game counters: gap, file and byte caps, one shop screenshot. A game time going back by
    more than 60 s (or a new game key) starts a new game."""

    def __init__(self) -> None:
        self.key: Any = None
        self.last_gt = -1.0
        self.last_t = -1e18
        self.files = 0
        self.bytes = 0
        self.shop_done = False

    def new_game(self, key: Any, game_time: float | None) -> None:
        gt = _f(game_time)
        if key != self.key or (gt is not None and gt < self.last_gt - 60.0):
            self.key, self.last_t, self.files, self.bytes, self.shop_done = key, -1e18, 0, 0, False
        if gt is not None:
            self.last_gt = gt

    def full(self) -> bool:
        return self.files >= MAX_FILES or self.bytes >= MAX_BYTES

    def crop_due(self, t: float, gap_s: float = MIN_GAP_S) -> bool:
        return not self.full() and t - self.last_t >= gap_s

    def add(self, n_bytes: int, t: float | None = None) -> None:
        self.files += 1
        self.bytes += max(0, int(n_bytes))
        if t is not None:
            self.last_t = t


__all__ = ["frame_reason", "GameBudget", "MIN_GAP_S", "MAX_FILES", "MAX_BYTES", "JPEG_Q", "REASONS"]
