"""Ward spots: a table of standard ward positions + when / where to ward (1-3 spots).

:data:`SPOTS` holds the classic Summoner's Rift ward spots in normalized minimap coordinates,
written from the BLUE side (``ORDER``) point of view and mirrored (``(1 - u, 1 - v)``, the map is
point-symmetric) for a red side player. Every spot was checked on the official minimap texture
(walkable pixel, not inside a wall: ``tests/test_wards.py``).

:func:`recommend` picks the 1-3 best spots for my role / side / situation:

* an objective spawning within 90 s (or up): the approaches of its pit first;
* laning phase: the river / lane brushes of my lane (top: river brush + tri-brush, mid: both
  pixel brushes + raptors entrance, bot: river brush + dragon front);
* the enemy jungler last seen on one side: the brushes on his way to my lane;
* ahead (stance / gold): deep wards in the enemy jungle; behind: defensive wards in my jungle;
* close spots preferred (I can walk there);
* Faelights ("lampes féeriques", season 2026, patch 26.1): fixed rings on the map where a ward
  gets +25 % vision radius and reveals a bonus area for 45 s. 8 exist from the start (one near
  each base gate (4), the river brushes at the top / bot ends of the river (2), the "banana"
  river-wall brushes across the Baron / dragon pits (2)) and 4 more appear when the Elemental
  Rift transforms (after the 2nd dragon: the krug brushes and the gromp side-lane brushes).
  They get a bonus score. Positions are APPROXIMATE (placed from the patch notes / wiki
  descriptions on the minimap texture, checked walkable), within ~2-3 % of the map width.

:class:`WardAdvisor` decides WHEN to show them (no trinket cooldown is known - nothing is read
from the game but the official API): when I come back to lane after a base, every
:data:`PERIODIC_S` while I am out on the map, and before an objective; plus one written
reminder line and the control ward reminder (none in my inventory). Pure Python, thread-safe,
never raises from its public methods.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Any, Iterable

from treeaicoach import geometry
from treeaicoach.fmtutil import finite as _f

log = logging.getLogger(__name__)

CONTROL_WARD = 2055
SHOW_AFTER_BASE_S = 25.0       # spots shown this long after I left my base
PERIODIC_S = 150.0             # ... and every ~2.5 min while out on the map
SHOW_PERIODIC_S = 18.0
OBJECTIVE_LEAD = (30.0, 90.0)  # ... and from 90 s to 30 s before an objective spawn
MAX_SPOTS = 3
REMIND_GAP_S = 150.0           # written reminder at most this often
CONTROL_REMIND_GAP_S = 300.0


@dataclass(frozen=True)
class WardSpot:
    id: str
    label: str                      # French ("buisson pixel (rivière du haut)")
    uv: tuple[float, float]         # BLUE side point of view
    area: str                       # "own" | "river" | "enemy"
    roles: frozenset = frozenset()  # roles for which it is a lane / routine spot
    objective: str | None = None    # "dragon" | "baron" (pit approaches)
    control: bool = False           # good control ward spot (brush / pit)
    faelight: bool = False          # a Faelight ring (2026): ward vision +25 % and a 45 s reveal
    after_rift: bool = False        # Faelight that only appears once the Elemental Rift transformed

    def uv_for(self, team: str | None) -> tuple[float, float]:
        if team != "CHAOS":
            return self.uv
        return RED_OVERRIDE.get(self.id) or (1.0 - self.uv[0], 1.0 - self.uv[1])

    def label_for(self, team: str | None) -> str:
        """French label from ``team``'s point of view (top / bottom swapped for the red side)."""
        if team != "CHAOS":
            return self.label
        return self.label.replace("haut", "\0").replace("bas", "haut").replace("\0", "bas")


def _r(*roles: str) -> frozenset:
    return frozenset(roles)


SPOTS: tuple[WardSpot, ...] = (
    WardSpot("pixel_top", "buisson pixel (rivière du haut)", (0.414, 0.395), "river", _r("MIDDLE", "JUNGLE", "UTILITY"),
             None, True),
    WardSpot("pixel_bot", "buisson pixel (rivière du bas)", (0.586, 0.605), "river", _r("MIDDLE", "JUNGLE", "UTILITY"),
             None, True),
    WardSpot("river_top_lane", "buisson de rivière en haut (lampe féerique)", (0.160, 0.221), "river", _r("TOP"),
             None, True, True),
    WardSpot("river_bot_lane", "buisson de rivière en bas (lampe féerique)", (0.793, 0.842), "river",
             _r("BOTTOM", "UTILITY"), None, True, True),
    WardSpot("tri_own", "tri-buisson de ta jungle", (0.137, 0.346), "own", _r("TOP", "JUNGLE"), None, True),
    WardSpot("raptors_own", "entrée des raptors", (0.439, 0.580), "own", _r("MIDDLE", "JUNGLE"), None, False),
    WardSpot("dragon_front", "devant le dragon (rivière)", (0.598, 0.678), "river", _r("BOTTOM", "UTILITY", "JUNGLE"),
             "dragon", True),
    WardSpot("dragon_own_entrance", "entrée du dragon de ton côté", (0.600, 0.740), "own", _r("UTILITY"), "dragon",
             False),
    WardSpot("dragon_enemy_entrance", "entrée du dragon côté ennemi", (0.699, 0.619), "enemy", _r(), "dragon", True),
    WardSpot("baron_front", "devant le Baron (rivière)", (0.373, 0.367), "river", _r("TOP", "JUNGLE"), "baron", True),
    WardSpot("baron_own_entrance", "entrée du Baron de ton côté", (0.301, 0.381), "own", _r(), "baron", False),
    WardSpot("baron_enemy_entrance", "entrée du Baron côté ennemi", (0.400, 0.260), "enemy", _r(), "baron", True),
    WardSpot("bluebuff_own", "ton buff bleu", (0.256, 0.473), "own", _r("JUNGLE"), None, False),
    WardSpot("redbuff_own", "ton buff rouge", (0.520, 0.730), "own", _r("JUNGLE"), None, False),
    WardSpot("tri_enemy", "tri-buisson ennemi (bas)", (0.887, 0.670), "enemy", _r("BOTTOM", "UTILITY"), None, True),
    WardSpot("raptors_enemy", "raptors ennemis", (0.561, 0.420), "enemy", _r("MIDDLE", "JUNGLE"), None, False),
    WardSpot("bluebuff_enemy", "buff bleu ennemi", (0.744, 0.527), "enemy", _r("JUNGLE"), None, False),
    WardSpot("redbuff_enemy", "buff rouge ennemi", (0.480, 0.270), "enemy", _r("JUNGLE"), None, False),
    # Faelights (2026, approximate positions - see the module doc)
    WardSpot("fae_banana_top", "lampe féerique du buisson-banane (rivière du haut)", (0.385, 0.425), "river",
             _r("MIDDLE", "JUNGLE"), "baron", True, True),
    WardSpot("fae_banana_bot", "lampe féerique du buisson-banane (rivière du bas)", (0.615, 0.575), "river",
             _r("MIDDLE", "JUNGLE", "UTILITY"), "dragon", True, True),
    WardSpot("fae_gate_top", "lampe féerique à la sortie de ta base (côté haut)", (0.190, 0.640), "own", _r(),
             None, False, True),
    WardSpot("fae_gate_bot", "lampe féerique à la sortie de ta base (côté bas)", (0.360, 0.810), "own", _r(),
             None, False, True),
    WardSpot("fae_gate_enemy_top", "lampe féerique à la sortie de leur base (côté haut)", (0.640, 0.190), "enemy",
             _r(), None, False, True),
    WardSpot("fae_gate_enemy_bot", "lampe féerique à la sortie de leur base (côté bas)", (0.810, 0.360), "enemy",
             _r(), None, False, True),
    WardSpot("fae_krugs_own", "lampe féerique près de tes krugs", (0.572, 0.861), "own", _r("BOTTOM", "UTILITY"),
             None, True, True, True),
    WardSpot("fae_gromp_own", "lampe féerique près de ton golem (voie du haut)", (0.135, 0.440), "own", _r("TOP"),
             None, True, True, True),
    WardSpot("fae_krugs_enemy", "lampe féerique près de leurs krugs", (0.428, 0.139), "enemy", _r("TOP"),
             None, True, True, True),
    WardSpot("fae_gromp_enemy", "lampe féerique près de leur golem (voie du bas)", (0.865, 0.560), "enemy",
             _r("BOTTOM", "UTILITY"), None, True, True, True),
)
FAELIGHT_BONUS = 8.0             # score bonus of a Faelight spot (bigger vision, 45 s reveal)
SPOT_BY_ID = {s.id: s for s in SPOTS}
#: The texture is not perfectly point-symmetric: red side spots moved onto walkable pixels.
RED_OVERRIDE: dict[str, tuple[float, float]] = {
    "river_top_lane": (0.834, 0.785), "tri_own": (0.848, 0.650), "dragon_front": (0.406, 0.322)}
OBJ_PIT = {"dragon": "dragon", "elder": "dragon", "baron": "baron", "herald": "baron", "grubs": "baron"}
ROLE_SIDE = {"TOP": "top", "BOTTOM": "bot", "UTILITY": "bot", "MIDDLE": "mid"}


@dataclass(frozen=True)
class WardPick:
    spot: WardSpot
    uv: tuple[float, float]         # for MY side (mirrored for red)
    score: float
    why: str = ""

    team: str | None = None

    @property
    def label(self) -> str:
        return self.spot.label_for(self.team)


def _side_of_uv(uv: tuple[float, float]) -> str:
    if abs(uv[0] + uv[1] - 1.0) < 0.12:
        return "mid"
    return geometry.side_of(*uv)


def recommend(team: str | None, role: str | None = None, *, phase: str = "laning",
              me_pos: tuple[float, float] | None = None, objective: tuple[str, float] | None = None,
              jungler_side: str | None = None, ahead: float = 0.0, n: int = MAX_SPOTS,
              exclude: Iterable[str] = (), rift: bool = False) -> list[WardPick]:
    """Best ward spots (``n`` at most), best first. ``objective`` = (key, seconds to spawn, <= 0 if up);
    ``jungler_side`` = "top" / "bot" where the enemy jungler was last seen; ``ahead`` > 0 when I /
    my team am ahead (stance score or gold), < 0 when behind; ``rift`` = the Elemental Rift has
    transformed (the 4 late Faelights exist). Never raises."""
    try:
        role = str(role or "").upper() or None
        team = geometry.normalize_team(team) or "ORDER"
        skip = set(exclude or ())
        obj_pit = None
        if objective is not None:
            key, rem = objective
            rem_f = _f(rem, 999.0) or 0.0
            if rem_f <= OBJECTIVE_LEAD[1]:
                obj_pit = OBJ_PIT.get(str(key))
        my_side = ROLE_SIDE.get(role or "")
        picks: list[WardPick] = []
        for s in SPOTS:
            if s.id in skip or (s.after_rift and not rift):
                continue
            uv = s.uv_for(team)
            side = _side_of_uv(uv)
            score = 0.0
            why = ""
            if obj_pit is not None and s.objective == obj_pit:
                score += 60.0 + (8.0 if s.area == "river" else 0.0)
                why = "objectif"
                if s.area == "enemy" and ahead < 0:
                    score -= 25.0
            if phase == "laning" and role in s.roles:
                score += 30.0
                why = why or "ta voie"
            elif phase != "laning" and obj_pit is None:
                # mid / late game: vision around the next fights (river + pit approaches)
                if s.area == "river":
                    score += 12.0
                if s.objective is not None:
                    score += 10.0
            if role == "JUNGLE" and s.area == "enemy" and ahead > 0:
                score += 10.0
            if jungler_side in ("top", "bot") and side == jungler_side and s.area in ("river", "own"):
                if my_side in (None, jungler_side) or role in ("JUNGLE", "MIDDLE", "UTILITY"):
                    score += 15.0
                    why = why or "jungler ennemi de ce côté"
            if s.area == "enemy":
                score += 18.0 if ahead > 1.0 else (-35.0 if ahead < -1.0 else -5.0)
            elif s.area == "own" and ahead < -1.0:
                score += 12.0
            if my_side in ("top", "bot") and phase == "laning" and side not in (my_side, "mid") and obj_pit is None:
                score -= 30.0                       # the other half of the map: not my job in lane
            if s.faelight and score > 0.0:
                score += FAELIGHT_BONUS
            if me_pos is not None:
                score -= 30.0 * geometry.dist(uv, me_pos)
            if score > 5.0:
                picks.append(WardPick(s, uv, round(score, 1), why, team))
        picks.sort(key=lambda p: -p.score)
        out: list[WardPick] = []
        for p in picks:                              # keep the picks apart (not 2 wards in one brush)
            if all(geometry.dist(p.uv, q.uv) > 0.08 for q in out):
                out.append(p)
            if len(out) >= max(1, int(n)):
                break
        return out
    except Exception:
        log.exception("wards.recommend failed")
        return []


@dataclass(frozen=True)
class WardAdvice:
    picks: tuple[WardPick, ...]
    reason: str                       # "base" | "periodic" | "objective"
    text: str | None = None           # written reminder (None = only the map icons)
    key: str = ""
    until: float = 0.0


class WardAdvisor:
    """When to show the ward spots (see the module docstring). Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._was_in_base: bool | None = None
            self._show: WardAdvice | None = None
            self._next_periodic: float | None = None
            self._last_text_t = -math.inf
            self._last_control_t = -math.inf
            self._obj_done: set = set()

    def current(self, t: float) -> WardAdvice | None:
        with self._lock:
            s = self._show
            return s if s is not None and t < s.until else None

    def update(self, t: float, game: Any, *, me_pos: tuple[float, float] | None, in_base: bool,
               role: str | None, phase: str = "laning", objectives: Iterable[Any] = (),
               jungler_side: str | None = None, ahead: float = 0.0, quiet: bool = False) -> WardAdvice | None:
        """One tick; returns a NEW advice to write (text) when one starts, else None. ``quiet``
        (fight / gank): nothing new is shown. Never raises."""
        try:
            with self._lock:
                return self._update(float(t), game, me_pos, bool(in_base), role, phase, list(objectives or []),
                                    jungler_side, float(ahead or 0.0), bool(quiet))
        except Exception:
            log.exception("WardAdvisor.update failed")
            return None

    def _update(self, t: float, game: Any, me_pos: Any, in_base: bool, role: str | None, phase: str,
                objectives: list[Any], jungler_side: str | None, ahead: float, quiet: bool) -> WardAdvice | None:
        me = getattr(game, "me", None)
        if me is None:
            return None
        dead = bool(getattr(me, "is_dead", False))
        team = getattr(me, "team", None)
        gt = _f(getattr(game, "game_time", None), 0.0) or 0.0
        was = self._was_in_base
        self._was_in_base = in_base
        if dead or in_base or me_pos is None:
            if dead or in_base:
                self._show = None
            return None
        if quiet or gt < 75.0:
            return None
        # soonest objective (spawning in 30..90 s)
        obj = None
        for o in objectives:
            if getattr(o, "alive", False):
                continue
            rem = _f(getattr(o, "remaining", None))
            key = str(getattr(o, "key", "") or "")
            if rem is not None and OBJECTIVE_LEAD[0] <= rem <= OBJECTIVE_LEAD[1] and key in OBJ_PIT \
                    and _involved(key, role, me_pos, gt):
                if obj is None or rem < obj[1]:
                    obj = (key, rem)
        reason = None
        spawn_id = (obj[0], int((gt + obj[1]) // 30)) if obj is not None else None
        if spawn_id is not None and spawn_id not in self._obj_done:
            reason = "objective"
            self._obj_done.add(spawn_id)
        elif was is True and not in_base:
            reason = "base"
        elif self._next_periodic is not None and t >= self._next_periodic:
            reason = "periodic"
        if self._next_periodic is None or reason is not None:
            self._next_periodic = t + PERIODIC_S
        if reason is None:
            return None
        picks = recommend(team, role, phase=phase, me_pos=me_pos, objective=obj, jungler_side=jungler_side,
                          ahead=ahead, n=2 if reason == "periodic" else MAX_SPOTS, rift=rift_transformed(game))
        if not picks:
            return None
        dur = {"base": SHOW_AFTER_BASE_S, "periodic": SHOW_PERIODIC_S}.get(reason, 30.0)
        if reason == "objective" and obj is not None:
            dur = max(15.0, obj[1] - OBJECTIVE_LEAD[0] + 10.0)
        has_control = CONTROL_WARD in [int(i) for i in (getattr(me, "items", None) or []) if isinstance(i, int)]
        text = None
        if t - self._last_text_t >= REMIND_GAP_S or reason == "objective":
            best = picks[0]
            if reason == "objective" and obj is not None:
                name = {"dragon": "le dragon", "elder": "l'ancestral", "baron": "le Baron", "herald": "le Héraut",
                        "grubs": "les larves"}.get(obj[0], "l'objectif")
                text = f"Balise {best.label} : avant {name}"
                if has_control and best.spot.control:
                    text += " (ta balise de contrôle)"
                text += "."
            else:
                text = f"Balise {best.label}"
            self._last_text_t = t
        if not has_control and t - self._last_control_t >= CONTROL_REMIND_GAP_S and gt >= 240.0 and reason == "base":
            self._last_control_t = t
            text = (text + " " if text else "") + "Pense à une balise de contrôle au prochain retour."
        self._show = WardAdvice(tuple(picks), reason, text, f"ward:{reason}:{picks[0].spot.id}", t + dur)
        return self._show if text else None


def _involved(key: str, role: Any, me_pos: Any, gt: float) -> bool:
    """My role plays this objective (or I stand near its pit): voice_policy.objective_involved."""
    try:
        from treeaicoach.voice_policy import objective_involved

        return objective_involved(f"objective_soon:{key}:60", role, me_pos, gt)
    except Exception:
        return True


def rift_transformed(game: Any) -> bool:
    """True once the Elemental Rift has transformed: 2 elemental dragons killed (Live Client
    ``DragonKill`` events; the 3rd dragon's element is then known and the map changes). Never raises."""
    try:
        n = 0
        for ev in getattr(game, "events", None) or ():
            if isinstance(ev, dict) and ev.get("EventName") == "DragonKill" \
                    and str(ev.get("DragonType") or "").casefold() != "elder":
                n += 1
        return n >= 2
    except Exception:
        return False


def faelight_spots(rift: bool = False) -> list[WardSpot]:
    """The Faelight spots present on the map (the 4 late ones only once the rift transformed)."""
    return [s for s in SPOTS if s.faelight and (rift or not s.after_rift)]


__all__ = ["WardSpot", "SPOTS", "SPOT_BY_ID", "recommend", "WardPick", "WardAdvisor", "WardAdvice", "CONTROL_WARD",
           "rift_transformed", "faelight_spots", "FAELIGHT_BONUS"]
