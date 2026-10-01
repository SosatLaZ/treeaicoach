""""Right place at the right time": where I SHOULD be vs where I am, by game phase.

:class:`PositionCoach` compares my tracked position with the place a Challenger coach would want
me at, for the current phase (:mod:`treeaicoach.phase`) and situation:

* **objective setup** (60-90 s before dragon / Baron / grubs / Herald / Elder, by role: bot side
  + jungle + mid for the dragon, top side + jungle + mid for grubs / Herald, everybody for Baron /
  Elder past the laning phase): "Va en bas : dragon dans 60 s" + arrow to the pit;
* **laning phase**: a laner out of his lane for a long time without a reason (not in base, no
  objective, not dead): "Phase de voie : retourne en haut, tu perds de l'or et de l'XP";
* **mid / late game**: alone in a side lane far from my team while >= 3 enemies are missing
  (their death timers short, Baron / Elder up) -> "Tu es seul en haut en fin de partie avec
  4 ennemis disparus : recule" (urgent, may be spoken); my team grouped elsewhere while nothing
  is happening on my side -> "Regroupe avec ton équipe au milieu";
* **after an ace / long enemy death timers**: handled by :class:`treeaicoach.phase.EndGameCaller`.

It also keeps the per-phase "key moments" score of the game (was I near the pit when an epic
monster was taken, time alone in a side lane in late game) for the live HUD and praises good
positioning ("Bien placé pour le dragon !"). Pure Python (+ geometry), thread-safe, never
raises from its public methods.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Iterable

from treeaicoach import geometry
from treeaicoach.fmtutil import finite as _f

log = logging.getLogger(__name__)

PIT_UV = {"dragon": (geometry.DRAGON_PIT[0], geometry.DRAGON_PIT[1]),
          "elder": (geometry.DRAGON_PIT[0], geometry.DRAGON_PIT[1]),
          "baron": (geometry.BARON_PIT[0], geometry.BARON_PIT[1]),
          "herald": (geometry.BARON_PIT[0], geometry.BARON_PIT[1]),
          "grubs": (geometry.BARON_PIT[0], geometry.BARON_PIT[1])}
OBJ_NAME = {"dragon": "dragon", "elder": "dragon ancestral", "baron": "Baron", "herald": "Héraut",
            "grubs": "larves"}
OBJ_SIDE = {"dragon": "bot", "elder": "bot", "baron": "top", "herald": "top", "grubs": "top"}
SIDE_FR = {"top": "en haut", "mid": "au milieu", "bot": "en bas"}
ROLE_LANE = {"TOP": "top", "MIDDLE": "mid", "BOTTOM": "bot", "UTILITY": "bot"}
SETUP_WINDOW = (25.0, 90.0)     # seconds before a spawn: be on the pit side
FAR_FROM_PIT = 0.33             # farther than this from the pit = not there
OUT_OF_LANE_S = 40.0            # laning: out of my lane this long
ALONE_R = 0.30                  # no ally within this = alone
MISSING_MIN = 3
SIDE_ALONE_CONFIRM_S = 3.0
GROUP_R = 0.20
GROUP_FAR = 0.40
ADVICE_COOLDOWN_S = {"objective": 75.0, "lane": 120.0, "alone": 40.0, "group": 120.0}
OBJ_EVENTS = {"DragonKill": "dragon", "BaronKill": "baron", "HeraldKill": "herald", "HordeKill": "grubs"}
PRESENT_R = 0.25                # near the pit when the monster dies
#: middle of MY half of each lane (arrow target of "retourne en voie")
LANE_POINT_BLUE = {"top": (0.085, 0.40), "mid": (0.42, 0.58), "bot": (0.60, 0.915)}
LANE_POINT_RED = {"top": (0.40, 0.085), "mid": (0.58, 0.42), "bot": (0.915, 0.60)}
PRAISE_FR = {"dragon": "Bien placé pour le dragon !", "elder": "Bien placé pour l'ancestral !",
             "baron": "Bien placé pour le Baron !", "herald": "Bien placé pour le Héraut !",
             "grubs": "Bien placé pour les larves !"}


def _side(uv: tuple[float, float]) -> str:
    if abs(uv[0] + uv[1] - 1.0) < 0.12:
        return "mid"
    return geometry.side_of(*uv)


@dataclass(frozen=True)
class PositionAdvice:
    key: str
    kind: str                          # "objective" | "lane" | "alone" | "group"
    text: str
    title: str
    target: tuple[float, float] | None = None
    speak: bool = False
    priority: int = 50
    t: float = 0.0


@dataclass
class PhaseScore:
    moments: int = 0                   # key moments (epic monster taken while I was alive)
    present: int = 0                   # ... where I was near the pit
    alone_s: float = 0.0               # late game: seconds alone in a side lane with enemies missing
    advice: int = 0                    # corrections given

    def pct(self) -> int | None:
        return int(round(100.0 * self.present / self.moments)) if self.moments else None


def roles_for(obj: str, phase: str) -> frozenset[str]:
    """Roles that should be on the pit side ``obj`` during ``phase``."""
    if obj in ("baron", "elder") or phase in ("late", "end"):
        return frozenset({"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"})
    if obj == "dragon":
        return frozenset({"JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"}) | (frozenset({"TOP"}) if phase == "mid" else frozenset())
    if obj in ("grubs", "herald"):
        return frozenset({"JUNGLE", "TOP", "MIDDLE"})
    return frozenset()


class PositionCoach:
    """See the module docstring. One instance per game."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._last: dict[str, float] = {}
            self._out_since: float | None = None
            self._alone_since: float | None = None
            self._last_t: float | None = None
            self._scores: dict[str, PhaseScore] = {}
            self._seen_events: set = set()
            self._praise: list[tuple[str, str]] = []
            self._my_hist: list[tuple[float, float, float]] = []   # (gt, u, v)

    def scores(self) -> dict[str, PhaseScore]:
        with self._lock:
            return {k: PhaseScore(v.moments, v.present, v.alone_s, v.advice) for k, v in self._scores.items()}

    def pop_praise(self) -> list[tuple[str, str]]:
        """``(key, text)`` of good positioning moments since the last call."""
        with self._lock:
            out, self._praise = self._praise, []
            return out

    def update(self, t: float, game: Any, st: Any, *, role: str | None, me_pos: tuple[float, float] | None,
               allies: Iterable[Any] = (), enemies: Iterable[Any] = (), objectives: Iterable[Any] = (),
               in_base: bool = False, quiet: bool = False) -> PositionAdvice | None:
        """One tick -> at most one advice (cooled down per kind). ``st`` = :class:`phase.MapState`;
        ``allies`` / ``enemies`` = :class:`treeaicoach.fight.Seen` lists. Never raises."""
        try:
            with self._lock:
                return self._update(float(t), game, st, str(role or "").upper() or None, me_pos, list(allies or []),
                                    list(enemies or []), list(objectives or []), bool(in_base), bool(quiet))
        except Exception:
            log.exception("PositionCoach.update failed")
            return None

    # ------------------------------------------------------------------ internals
    def _score(self, phase: str) -> PhaseScore:
        return self._scores.setdefault(phase, PhaseScore())

    def _moments(self, game: Any, st: Any, gt: float) -> None:
        """Epic monster taken: was I near the pit? (+ praise when my team took it with me there)."""
        me = getattr(game, "me", None)
        my_team = getattr(st, "my_team", None)
        names = {}
        for p in (game.all_players() if hasattr(game, "all_players") else []):
            for n in (getattr(p, "riot_id", ""), getattr(p, "summoner_name", "")):
                if n:
                    names[str(n).split("#")[0].casefold()] = getattr(p, "team", "")
        for ev in getattr(game, "events", None) or []:
            if not isinstance(ev, dict):
                continue
            kind = OBJ_EVENTS.get(str(ev.get("EventName") or ""))
            if kind is None:
                continue
            T = _f(ev.get("EventTime"))
            uid = (ev.get("EventID"), ev.get("EventName"), T)
            if T is None or uid in self._seen_events:
                continue
            self._seen_events.add(uid)
            if gt - T > 20.0:
                continue                                    # joined late: old events are not scored
            if str(ev.get("DragonType") or "").casefold() == "elder":
                kind = "elder"
            pit = PIT_UV.get(kind)
            if pit is None or me is None or bool(getattr(me, "is_dead", False)):
                continue
            pos = self._pos_at(T)
            phase = getattr(st, "phase", "laning")
            sc = self._score(phase)
            role = getattr(self, "_role", None) or getattr(me, "position", "")   # resolved role (lane swaps)
            in_roles = role in roles_for(kind, phase) or phase != "laning"
            if not in_roles:
                continue
            sc.moments += 1
            near = pos is not None and geometry.dist(pos, pit) <= PRESENT_R
            if near:
                sc.present += 1
                team = names.get(str(ev.get("KillerName") or "").split("#")[0].casefold())
                if team == my_team:
                    self._praise.append((f"pos:{kind}:{int(T)}", PRAISE_FR.get(kind, "Bien placé !")))

    def _pos_at(self, gt: float) -> tuple[float, float] | None:
        best = None
        for T, u, v in reversed(self._my_hist):
            if abs(T - gt) <= 8.0 and (best is None or abs(T - gt) < abs(best[0] - gt)):
                best = (T, u, v)
            if T < gt - 8.0:
                break
        return (best[1], best[2]) if best is not None else None

    def _ok(self, kind: str, t: float) -> bool:
        last = self._last.get(kind)
        return last is None or t - last >= ADVICE_COOLDOWN_S.get(kind, 90.0)

    def _update(self, t: float, game: Any, st: Any, role: str | None, me_pos: Any, allies: list[Any],
                enemies: list[Any], objectives: list[Any], in_base: bool, quiet: bool) -> PositionAdvice | None:
        me = getattr(game, "me", None)
        if me is None or st is None:
            return None
        gt = _f(getattr(st, "gt", None), 0.0) or 0.0
        dt = 0.0 if self._last_t is None else max(0.0, min(2.0, t - self._last_t))
        self._last_t = t
        if me_pos is not None:
            if not self._my_hist or gt - self._my_hist[-1][0] >= 1.0:
                self._my_hist.append((gt, float(me_pos[0]), float(me_pos[1])))
                del self._my_hist[:-240]
        self._role = role
        self._moments(game, st, gt)
        phase = getattr(st, "phase", "laning")
        if bool(getattr(me, "is_dead", False)) or me_pos is None or in_base:
            self._out_since = None
            self._alone_since = None
            return None
        team = getattr(st, "my_team", None)
        dead_en = {str(d.alias).lower() for d in getattr(st, "enemies_dead", ()) or ()}
        missing = [e for e in enemies if not getattr(e, "visible", False)
                   and str(getattr(e, "alias", "") or "").lower() not in dead_en]
        # unseen enemies never tracked count as missing too
        n_known = len({str(getattr(e, "alias", "") or "").lower() for e in enemies if getattr(e, "alias", None)})
        n_missing = len(missing) + max(0, 5 - len(dead_en) - max(n_known, len(enemies)))
        near_allies = [a for a in allies if getattr(a, "uv", None) is not None and geometry.dist(a.uv, me_pos) < ALONE_R]
        zone = geometry.classify_zone(*me_pos)
        lane = geometry.lane_of(zone)
        cands: list[PositionAdvice] = []
        # ---- late game: alone in a side lane with enemies missing
        alone_risk = (phase in ("late", "end") or (phase == "mid" and gt >= 1200.0)) and lane in ("top", "bot") \
            and not near_allies and n_missing >= MISSING_MIN
        if alone_risk:
            base = {"ORDER": geometry.BLUE_FOUNTAIN, "CHAOS": geometry.RED_FOUNTAIN}.get(team or "", (0.5, 0.5))
            deep = geometry.dist(me_pos, base) > 0.55       # past the middle of the lane
            if self._alone_since is None:
                self._alone_since = t
            self._score(phase).alone_s += dt
            if deep and t - self._alone_since >= SIDE_ALONE_CONFIRM_S:
                extra = ""
                if getattr(st, "baron_team", None) is None and any(
                        str(getattr(o, "key", "")) in ("baron", "elder") and getattr(o, "alive", False) for o in objectives):
                    extra = ", Baron / ancestral dispo"
                safe = None
                if allies:
                    pts = [a.uv for a in allies if getattr(a, "uv", None) is not None]
                    if pts:
                        safe = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
                if safe is None and hasattr(st, "nearest_safe_uv"):
                    safe = st.nearest_safe_uv(me_pos)
                when = "en fin de partie" if phase in ("late", "end") else "à ce stade"
                cands.append(PositionAdvice("pos:alone", "alone",
                                            f"Tu es seul {SIDE_FR[lane]} {when} avec {min(n_missing, 5)} ennemis "
                                            f"disparus{extra} : recule.", "RECULE", safe, True, 90, t))
        else:
            self._alone_since = None
        # ---- objective setup
        obj = None
        for o in objectives:
            key = str(getattr(o, "key", "") or "")
            if key not in PIT_UV or getattr(o, "alive", False):
                continue
            rem = _f(getattr(o, "remaining", None))
            if rem is not None and SETUP_WINDOW[0] <= rem <= SETUP_WINDOW[1] and (obj is None or rem < obj[1]):
                obj = (key, rem)
        if obj is not None and role in roles_for(obj[0], phase):
            pit = PIT_UV[obj[0]]
            if geometry.dist(me_pos, pit) > FAR_FROM_PIT:
                side = OBJ_SIDE.get(obj[0], "bot")
                secs = int(round(obj[1] / 5.0) * 5)
                name = OBJ_NAME.get(obj[0], obj[0])
                urgent = obj[1] <= 65 and geometry.dist(me_pos, pit) > 0.5 and phase != "laning"
                cands.append(PositionAdvice(f"pos:obj:{obj[0]}", "objective", f"Va {SIDE_FR[side]} : {name} dans {secs} s",
                                            f"{name.upper()}", pit, urgent, 80, t))
        # ---- laning: out of my lane
        my_lane = ROLE_LANE.get(role or "")
        if phase == "laning" and role in ("TOP", "MIDDLE", "BOTTOM") and gt >= 120.0 and obj is None:
            if lane != my_lane and not geometry.is_base(zone):
                if self._out_since is None:
                    self._out_since = t
                if t - self._out_since >= OUT_OF_LANE_S:
                    target = _lane_point(my_lane, team)
                    cands.append(PositionAdvice("pos:lane", "lane", f"Phase de voie : retourne {SIDE_FR[my_lane]}, "
                                                "tu perds de l'or et de l'expérience.", f"RETOURNE {my_lane.upper()}",
                                                target, False, 40, t))
            else:
                self._out_since = None
        else:
            self._out_since = None
        # ---- mid / late game: team grouped elsewhere
        if phase in ("mid", "late", "end") and obj is None and not alone_risk:
            pts = [a.uv for a in allies if getattr(a, "uv", None) is not None]
            if len(pts) >= 3:
                cu, cv = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
                grouped = sum(1 for p in pts if geometry.dist(p, (cu, cv)) < GROUP_R) >= 3
                splitter = False
                try:
                    from treeaicoach.meta import profile

                    splitter = profile(getattr(me, "champion_alias", "")).has("splitpush") and n_missing < MISSING_MIN
                except Exception:
                    pass
                if grouped and geometry.dist(me_pos, (cu, cv)) > GROUP_FAR and not splitter:
                    where = SIDE_FR[_side((cu, cv))]
                    cands.append(PositionAdvice("pos:group", "group", f"Ton équipe est groupée {where} : regroupe-toi.",
                                                "REGROUPE", (cu, cv), False, 55, t))
        if quiet:
            cands = [c for c in cands if c.kind == "alone"]
        for c in sorted(cands, key=lambda c: -c.priority):
            if self._ok(c.kind, t):
                self._last[c.kind] = t
                self._score(phase).advice += 1
                return c
        return None


def _lane_point(lane: str | None, team: str | None) -> tuple[float, float] | None:
    """A point in the middle of my side of ``lane`` (arrow target)."""
    table = LANE_POINT_RED if team == "CHAOS" else LANE_POINT_BLUE
    return table.get(lane or "")


__all__ = ["PositionCoach", "PositionAdvice", "PhaseScore", "roles_for"]
