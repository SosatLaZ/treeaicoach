"""Ward spots: a table of standard ward positions + when / where to ward (1-3 spots).

:data:`SPOTS` comes from ``assets/ward_spots.json`` (schema 2): the Summoner's Rift ward spots in
ABSOLUTE normalized minimap coordinates (the minimap is never flipped for the red team, lanes and
epic pits are the same for both teams), each with the jungle it belongs to (``side``: blue / red /
None = river). :meth:`WardSpot.area_for` turns that into own / enemy / river for a team and
:meth:`WardSpot.label_for` gives the French label from that team's point of view. Until 2.2 the
spots were written from the blue side and point-mirrored for red players, which put the "dragon"
spots of a red player in front of Baron and gave a red top laner the bottom river brush.

Every spot sits on a walkable pixel of the official minimap texture (5x5 erosion,
``tests/test_voice_gate_wards.py``, ``tools/validate_data.py``) and carries its relevance: roles,
game phases (``laning`` / ``mid`` / ``late``) and a time window (``from_s`` / ``until_s``).

:func:`recommend` picks the 1-3 best spots for my role / side / situation:

* an objective spawning within 90 s (or up): the approaches of its pit first;
* laning phase: the river / lane brushes of my lane (top: river brush + tri-brush, mid: both
  pixel brushes + raptors entrance, bot: river brush + dragon front);
* the enemy jungler last seen on one side: the brushes on his way to my lane;
* ahead (stance / gold): deep wards in the enemy jungle; behind: defensive wards in my jungle;
* close spots preferred (I can walk there); spots outside their phase / time window lose points;
* Faelights ("lampes féeriques", season 2026, patch 26.1): pads where a ward gets +25 % vision
  radius and reveals a bonus area for 45 s. 8 exist from the start (one near each base gate (4),
  the river brushes at the top / bot ends of the river (2), the river-wall brushes across the
  Baron / dragon pits (2)) and 4 more appear when the Elemental Rift transforms (after the 2nd
  dragon: the krug brushes and the gromp side-lane brushes). They get a bonus score. Their
  positions are EXACT: the pads of the game files (``tools/fetch_map.py``, CommunityDragon).

:class:`WardAdvisor` decides WHEN to show them (no trinket cooldown is known - nothing is read
from the game but the official API): when I come back to lane after a base, every
:data:`PERIODIC_S` while I am out on the map, and before an objective; plus one written
reminder line and the control ward reminder (none in my inventory). Pure Python, thread-safe,
never raises from its public methods.
"""

from __future__ import annotations

import json
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
SPOTS_FILE = "ward_spots.json"
PHASES = ("laning", "mid", "late")
OFF_PHASE_PENALTY = 20.0       # a spot that does not matter in this phase
TEAM_SIDE = {"ORDER": "blue", "CHAOS": "red"}


@dataclass(frozen=True)
class WardSpot:
    id: str
    label: str                      # French, for a BLUE player ("tri-buisson de ta jungle (haut)")
    uv: tuple[float, float]         # absolute minimap position (same for both teams)
    area: str                       # BLUE player's view: "own" | "river" | "enemy" (see area_for)
    roles: frozenset = frozenset()  # roles for which it is a lane / routine spot
    objective: str | None = None    # "dragon" | "baron" (pit approaches)
    control: bool = False           # good control ward spot (brush / pit)
    faelight: bool = False          # a Faelight pad (2026): ward vision +25 % and a 45 s reveal
    after_rift: bool = False        # Faelight that only appears once the Elemental Rift transformed
    side: str | None = None         # jungle it belongs to: "blue" | "red" | None (river)
    label_red: str = ""             # French label for a RED player ("" = same as ``label``)
    phases: tuple[str, ...] = PHASES
    from_s: float = 0.0             # not worth warding before this game time
    until_s: float | None = None
    src: str = ""                   # "game" (game files) | "manual"

    def uv_for(self, team: str | None) -> tuple[float, float]:
        """Position on the minimap: absolute, the same for both teams (kept for the callers)."""
        return self.uv

    def area_for(self, team: str | None) -> str:
        """"own" / "enemy" / "river" from ``team``'s point of view."""
        if self.side is None:
            return "river"
        mine = TEAM_SIDE.get(str(team or "ORDER"), "blue")
        return "own" if self.side == mine else "enemy"

    def label_for(self, team: str | None) -> str:
        """French label from ``team``'s point of view."""
        return (self.label_red or self.label) if team == "CHAOS" else self.label

    def relevant(self, phase: str | None = None, gt: float | None = None) -> bool:
        """Does the spot matter in this phase (``phase.py``) / at this game time (s)?"""
        if phase in PHASES and self.phases and phase not in self.phases:
            return False
        if gt is not None:
            try:
                g = float(gt)
            except (TypeError, ValueError):
                return True
            if g < self.from_s or (self.until_s is not None and g > self.until_s):
                return False
        return True


def _spot(d: dict) -> WardSpot | None:
    try:
        u, v = (float(x) for x in d["uv"])
        if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
            return None
        side = d.get("side") if d.get("side") in ("blue", "red") else None
        area = "river" if side is None else ("own" if side == "blue" else "enemy")
        phases = tuple(p for p in (d.get("phases") or PHASES) if p in PHASES) or PHASES
        until = d.get("until_s")
        return WardSpot(id=str(d["id"]), label=str(d["label"]), uv=(round(u, 4), round(v, 4)), area=area,
                        roles=frozenset(str(r) for r in d.get("roles") or ()), objective=d.get("objective") or None,
                        control=bool(d.get("control")), faelight=bool(d.get("faelight")),
                        after_rift=bool(d.get("after_rift")), side=side, label_red=str(d.get("label_red") or ""),
                        phases=phases, from_s=float(d.get("from_s") or 0.0),
                        until_s=float(until) if isinstance(until, (int, float)) else None, src=str(d.get("src") or ""))
    except (KeyError, TypeError, ValueError):
        return None


def load_spots(data: Any = None) -> tuple[WardSpot, ...]:
    """Spots of ``data`` (the ``assets/ward_spots.json`` format; default: the bundled file). Never raises."""
    try:
        if data is None:
            from treeaicoach.paths import asset_path

            data = json.loads(asset_path(SPOTS_FILE).read_text(encoding="utf-8"))
        out = [s for s in (_spot(d) for d in (data.get("spots") or []) if isinstance(d, dict)) if s is not None]
        return tuple(out)
    except Exception:
        log.warning("Ward spots unavailable (assets/%s)", SPOTS_FILE, exc_info=True)
        return ()


SPOTS: tuple[WardSpot, ...] = load_spots()
FAELIGHT_BONUS = 8.0             # score bonus of a Faelight spot (bigger vision, 45 s reveal)
SPOT_BY_ID = {s.id: s for s in SPOTS}
OBJ_PIT = {"dragon": "dragon", "elder": "dragon", "baron": "baron", "herald": "baron", "grubs": "baron"}
ROLE_SIDE = {"TOP": "top", "BOTTOM": "bot", "UTILITY": "bot", "MIDDLE": "mid"}


@dataclass(frozen=True)
class WardPick:
    spot: WardSpot
    uv: tuple[float, float]         # minimap position (absolute)
    score: float
    why: str = ""

    team: str | None = None

    @property
    def label(self) -> str:
        return self.spot.label_for(self.team)

    @property
    def area(self) -> str:
        """"own" / "enemy" / "river" for MY team."""
        return self.spot.area_for(self.team)


def _side_of_uv(uv: tuple[float, float]) -> str:
    if abs(uv[0] + uv[1] - 1.0) < 0.12:
        return "mid"
    return geometry.side_of(*uv)


def recommend(team: str | None, role: str | None = None, *, phase: str = "laning",
              me_pos: tuple[float, float] | None = None, objective: tuple[str, float] | None = None,
              jungler_side: str | None = None, ahead: float = 0.0, n: int = MAX_SPOTS,
              exclude: Iterable[str] = (), rift: bool = False, gt: float | None = None) -> list[WardPick]:
    """Best ward spots (``n`` at most), best first. ``objective`` = (key, seconds to spawn, <= 0 if up);
    ``jungler_side`` = "top" / "bot" where the enemy jungler was last seen; ``ahead`` > 0 when I /
    my team am ahead (stance score or gold), < 0 when behind; ``rift`` = the Elemental Rift has
    transformed (the 4 late Faelights exist); ``gt`` = game time (s): spots outside their time
    window / phase lose points (an objective spot stays valid before its pit). Never raises."""
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
            area = s.area_for(team)
            score = 0.0
            why = ""
            obj_spot = obj_pit is not None and s.objective == obj_pit
            if obj_spot:
                score += 60.0 + (8.0 if area == "river" else 0.0)
                why = "objectif"
                if area == "enemy" and ahead < 0:
                    score -= 25.0
            elif not s.relevant(phase, gt):
                score -= OFF_PHASE_PENALTY
            if phase == "laning" and role in s.roles:
                score += 30.0
                why = why or "ta voie"
            elif phase != "laning" and obj_pit is None:
                # mid / late game: vision around the next fights (river + pit approaches)
                if area == "river":
                    score += 12.0
                if s.objective is not None:
                    score += 10.0
            if role == "JUNGLE" and area == "enemy" and ahead > 0:
                score += 10.0
            if jungler_side in ("top", "bot") and side == jungler_side and area in ("river", "own"):
                if my_side in (None, jungler_side) or role in ("JUNGLE", "MIDDLE", "UTILITY"):
                    score += 15.0
                    why = why or "jungler ennemi de ce côté"
            if area == "enemy":
                score += 18.0 if ahead > 1.0 else (-35.0 if ahead < -1.0 else -5.0)
            elif area == "own" and ahead < -1.0:
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
                          ahead=ahead, n=2 if reason == "periodic" else MAX_SPOTS, rift=rift_transformed(game), gt=gt)
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
            if not text:                 # one instruction per card: the ward spot first, the reminder alone
                text = "Achète une balise de contrôle au prochain retour."
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
           "rift_transformed", "faelight_spots", "FAELIGHT_BONUS", "load_spots", "PHASES"]
