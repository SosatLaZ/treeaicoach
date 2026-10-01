"""Fight awareness: skirmish / teamfight detection and ONE clear call (ENGAGE / REPLI).

During a fight the player needs one decision, not a stream of gank beeps. :class:`FightTracker`
looks at the tracked minimap icons (positions of me, my allies and the visible enemies) and at
the public Live Client data (levels, items -> item gold, deaths and respawn timers, my own HP
from ``activePlayer.championStats``, the ``ChampionKill`` event feed) once per tick:

* **Detection** - a fight starts when, around me (:data:`FIGHT_R`, ~2200 game units):
  >= 2 of us (me included) and >= 2 enemies are visible, or 4+ champions with at least one
  enemy, or >= 2 enemies inside the danger radius, or a champion died in the last seconds next
  to me (event feed) with an enemy still close. It must hold :data:`ENTER_S`; it ends after
  :data:`EXIT_S` without any fight condition (or when I die).
* **Power estimate** - every participant gets ``1 + 0.12 (level - 1) + 0.11 item_gold / 1000``
  times its power curve (:mod:`treeaicoach.meta`: early / late champions) and, for me, my HP
  share; allies / enemies close but not yet in the fight count 60 %, hidden enemies seen near
  the fight in the last 15 s 50 % (they can join), dead ones 0, engage champions +4 %.
* **Call** - ``ratio = allies / enemies``: ENGAGE when >= :data:`ENGAGE_RATIO` (and I have
  > 35 % HP), REPLI when <= :data:`RETREAT_RATIO` or when I am below 25 % HP; in between the
  previous call is kept (hysteresis). A call changes at most every :data:`CALL_GAP_S` (4 s).
  No target selection, no cooldown tracking: only public / visible data.
* **Summary** - when the fight ends: kills / deaths of each side during the fight (event
  feed) -> one written line ("Combat gagné 3 pour 1 !").

Pure Python (+ geometry / meta), thread-safe, never raises from its public methods.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable

from treeaicoach import geometry

log = logging.getLogger(__name__)

FIGHT_R = 0.15                 # "around me" for the fight (normalized minimap)
JOIN_R = 0.30                  # close enough to join within a few seconds
DANGER_R = 0.10                # 2 enemies this close to me = I am being dived
KILL_RECENT_S = 6.0            # a death this recent (game time) next to me = fight
ENTER_S = 0.4                  # the fight condition must hold this long
EXIT_S = 4.0                   # ... and be gone this long to end the fight
CALL_GAP_S = 4.0               # min time between two call changes
ENGAGE_RATIO = 1.20
RETREAT_RATIO = 0.85
LOW_HP = 0.25
ENGAGE_MIN_HP = 0.35
HIDDEN_JOIN_S = 15.0           # a hidden enemy seen near the fight this recently may join
NEAR_W, HIDDEN_W = 0.6, 0.5
CALL_WORD = {"engage": "Engage !", "retreat": "Repli !"}
CALL_TITLE = {"engage": "ENGAGE", "retreat": "REPLI"}


def _f(x: Any, default: float | None = None) -> float | None:
    if x is None or isinstance(x, bool):
        return default
    try:
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        return default
    return v if math.isfinite(v) else default


def _uv(p: Any) -> tuple[float, float] | None:
    try:
        u, v = _f(p[0]), _f(p[1])
    except (TypeError, IndexError, KeyError):
        return None
    return (u, v) if u is not None and v is not None else None


def champion_power(level: Any, item_gold: Any, alias: str | None = None, game_time: float = 0.0,
                   hp_frac: float | None = None) -> float:
    """Relative strength of one champion (~1 at level 1 without items, ~4 at 18 full build)."""
    lvl = min(18.0, max(1.0, _f(level, 1.0) or 1.0))
    gold = max(0.0, _f(item_gold, 0.0) or 0.0)
    p = 1.0 + 0.12 * (lvl - 1.0) + 0.11 * gold / 1000.0
    if alias:
        try:
            from treeaicoach.meta import profile

            p *= profile(alias).curve_factor(game_time)
        except Exception:
            pass
    if hp_frac is not None:
        h = min(1.0, max(0.0, float(hp_frac)))
        p *= 0.3 + 0.7 * h
    return p


@dataclass(frozen=True)
class Seen:
    """One champion as the fight logic sees it."""

    alias: str | None
    uv: tuple[float, float] | None
    visible: bool = True
    hidden_s: float = 0.0


@dataclass(frozen=True)
class FightState:
    active: bool = False
    kind: str | None = None                  # "skirmish" | "teamfight"
    centre: tuple[float, float] | None = None
    allies: int = 0                          # fighters on my side (me included)
    enemies: int = 0
    ally_power: float = 0.0
    enemy_power: float = 0.0
    ratio: float = 1.0
    call: str | None = None                  # "engage" | "retreat" | None
    reason: str = ""                         # short French reason ("3v2 · +2 niv")
    safe_uv: tuple[float, float] | None = None   # where to retreat (minimap arrow)
    since: float | None = None
    call_t: float | None = None

    @property
    def title(self) -> str | None:
        return CALL_TITLE.get(self.call or "")


@dataclass(frozen=True)
class FightUpdate:
    state: FightState
    new_call: str | None = None              # the call just changed to this ("engage" / "retreat")
    started: bool = False
    ended_summary: str | None = None         # one line when a fight just ended
    won: bool | None = None


def snapshot(tracker: Any, now: float) -> tuple[tuple[float, float] | None, list[Seen], list[Seen]]:
    """(me uv, allies, enemies) from a :class:`treeaicoach.tracker.Tracker`. Never raises."""
    me_uv = None
    allies: list[Seen] = []
    enemies: list[Seen] = []
    try:
        me = tracker.me()
        if me is not None and (me.visible or now - me.last_seen < 2.0):
            me_uv = _uv(me.position())
        for tr in tracker.allies(visible_only=True) or []:
            allies.append(Seen(tr.alias, _uv(tr.position()), True, 0.0))
        for tr in tracker.enemies(visible_only=False) or []:
            hid = 0.0 if tr.visible else max(0.0, now - float(tr.last_seen))
            enemies.append(Seen(tr.alias, _uv(tr.position()), bool(tr.visible), hid))
    except Exception:
        log.debug("fight.snapshot failed", exc_info=True)
    return me_uv, allies, enemies


def _norm(name: Any) -> str:
    return " ".join(str(name or "").split("#", 1)[0].split()).casefold()


class FightTracker:
    """See the module docstring. One instance per game (``reset()`` on a new game)."""

    def __init__(self, danger_r: float = DANGER_R) -> None:
        self._lock = threading.Lock()
        self._danger_r = danger_r
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._state = FightState()
            self._cond_since: float | None = None
            self._last_cond: float | None = None
            self._start_gt: float | None = None
            self._call: str | None = None
            self._call_t: float | None = None
            self._cand: tuple[str, float] | None = None

    def state(self) -> FightState:
        with self._lock:
            return self._state

    def in_fight(self) -> bool:
        with self._lock:
            return self._state.active

    # ------------------------------------------------------------------ main
    def update(self, t: float, game: Any, me_uv: tuple[float, float] | None, allies: Iterable[Seen],
               enemies: Iterable[Seen], *, scoreboard: Any = None, map_state: Any = None,
               lane_opponents: Iterable[str] = (), gt: float | None = None) -> FightUpdate:
        try:
            with self._lock:
                return self._update(float(t), game, _uv(me_uv) if me_uv is not None else None,
                                    list(allies or []), list(enemies or []), scoreboard, map_state,
                                    {str(a).lower() for a in lane_opponents or ()}, gt)
        except Exception:
            log.exception("FightTracker.update failed")
            return FightUpdate(self._state)

    def _players(self, game: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for p in (game.all_players() if game is not None and hasattr(game, "all_players") else []):
            a = str(getattr(p, "champion_alias", "") or "").lower()
            if a:
                out[a] = p
        return out

    def _gold(self, alias: str, p: Any, scoreboard: Any) -> float:
        for line in getattr(scoreboard, "players", None) or ():
            if str(getattr(line, "alias", "")).lower() == alias:
                return float(getattr(line, "item_gold", 0) or 0)
        try:
            from treeaicoach.scoreboard import items_gold

            return float(items_gold(getattr(p, "items", None) or ()))
        except Exception:
            return 0.0

    def _recent_kill_near(self, game: Any, gt: float, me_uv: Any, allies: list[Seen],
                          players: dict[str, Any]) -> bool:
        names_near: set[str] = set()
        me = getattr(game, "me", None)
        if me is not None:
            names_near |= {_norm(me.riot_id), _norm(me.summoner_name)}
        near_aliases = {str(a.alias).lower() for a in allies
                        if a.alias and a.uv is not None and me_uv is not None and geometry.dist(a.uv, me_uv) < FIGHT_R}
        for a in near_aliases:
            p = players.get(a)
            if p is not None:
                names_near |= {_norm(getattr(p, "riot_id", "")), _norm(getattr(p, "summoner_name", ""))}
        names_near.discard("")
        for ev in reversed(getattr(game, "events", None) or []):
            if not isinstance(ev, dict) or ev.get("EventName") != "ChampionKill":
                continue
            T = _f(ev.get("EventTime"))
            if T is None or gt - T > KILL_RECENT_S:
                break
            people = {_norm(ev.get("KillerName")), _norm(ev.get("VictimName"))} | {
                _norm(x) for x in (ev.get("Assisters") or []) if isinstance(x, str)}
            if people & names_near:
                return True
        return False

    def _update(self, t: float, game: Any, me_uv: tuple[float, float] | None, allies: list[Seen],
                enemies: list[Seen], scoreboard: Any, st_map: Any, lane_opps: set[str],
                gt_in: float | None) -> FightUpdate:
        me = getattr(game, "me", None) if game is not None else None
        gt = _f(gt_in) if gt_in is not None else (_f(getattr(game, "game_time", None), 0.0) or 0.0)
        gt = gt or 0.0
        dead = bool(getattr(me, "is_dead", False)) if me is not None else True
        players = self._players(game)
        dead_aliases = {a for a, p in players.items() if bool(getattr(p, "is_dead", False))}
        vis_en = [e for e in enemies if e.visible and e.uv is not None
                  and str(e.alias or "").lower() not in dead_aliases]
        vis_al = [a for a in allies if a.uv is not None and str(a.alias or "").lower() not in dead_aliases]
        cond = False
        centre = me_uv
        n_en = n_al = 0
        if me_uv is not None and not dead:
            near_en = [e for e in vis_en if geometry.dist(e.uv, me_uv) < FIGHT_R]
            near_al = [a for a in vis_al if geometry.dist(a.uv, me_uv) < FIGHT_R]
            n_en, n_al = len(near_en), 1 + len(near_al)
            in_danger = [e for e in vis_en if geometry.dist(e.uv, me_uv) < self._danger_r]
            in_base = False
            try:
                z = geometry.classify_zone(*me_uv)
                in_base = geometry.is_base(z) and geometry.zone_owner(z) == geometry.normalize_team(
                    getattr(me, "team", None))
            except Exception:
                pass
            laning = getattr(st_map, "phase", "laning") == "laning"
            non_lane = [e for e in near_en if str(e.alias or "").lower() not in lane_opps] if laning else near_en
            if not in_base:
                if n_al >= 2 and n_en >= 2:
                    cond = True
                elif n_al + n_en >= 4 and n_en >= 1 and (non_lane or not laning):
                    cond = True
                elif len(in_danger) >= 2:
                    cond = True
                elif n_en >= 1 and self._recent_kill_near(game, gt, me_uv, vis_al, players):
                    cond = True
            if near_en:
                pts = [e.uv for e in near_en] + [me_uv]
                centre = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        prev = self._state
        started = False
        if cond:
            self._last_cond = t
            if self._cond_since is None:
                self._cond_since = t
        else:
            self._cond_since = None
        active = prev.active
        if not active and cond and self._cond_since is not None and t - self._cond_since >= ENTER_S:
            active, started = True, True
            self._start_gt = gt
            self._call, self._call_t, self._cand = None, None, None
        ended_summary = None
        won = None
        if active and (dead or self._last_cond is None or t - self._last_cond >= EXIT_S):
            active = False
            ended_summary, won = self._summary(game, players, gt)
            self._call, self._call_t, self._cand = None, None, None
        if not active:
            self._state = FightState(active=False)
            return FightUpdate(self._state, None, False, ended_summary, won)
        # ---- power estimate
        hp = None
        stats = getattr(game, "champion_stats", None) or {}
        cur, mx = _f(stats.get("currentHealth")), _f(stats.get("maxHealth"))
        if cur is not None and mx and mx > 0:
            hp = max(0.0, min(1.0, cur / mx))
        ref = centre or me_uv

        def power(alias: str | None, hp_frac: float | None = None) -> float:
            a = str(alias or "").lower()
            p = players.get(a)
            if p is None:
                return champion_power(_avg_level(players), 0.0, None, gt, hp_frac) * 0.95
            return champion_power(getattr(p, "level", 1), self._gold(a, p, scoreboard), p.champion_alias, gt,
                                  hp_frac)

        me_alias = str(getattr(me, "champion_alias", "") or "")
        A = power(me_alias, hp)
        allies_in = 1
        styles_al: list[str] = [me_alias]
        for a in vis_al:
            d = geometry.dist(a.uv, ref)
            if d < FIGHT_R:
                A += power(a.alias)
                allies_in += 1
                styles_al.append(str(a.alias or ""))
            elif d < JOIN_R:
                A += NEAR_W * power(a.alias)
        E = 0.0
        enemies_in = 0
        styles_en: list[str] = []
        for e in enemies:
            a = str(e.alias or "").lower()
            if a and a in dead_aliases:
                continue
            if e.uv is None:
                continue
            d = geometry.dist(e.uv, ref)
            if e.visible:
                if d < FIGHT_R:
                    E += power(e.alias)
                    enemies_in += 1
                    styles_en.append(str(e.alias or ""))
                elif d < JOIN_R:
                    E += NEAR_W * power(e.alias)
            elif e.hidden_s <= HIDDEN_JOIN_S and d < JOIN_R:
                E += HIDDEN_W * power(e.alias)
        A *= 1.0 + min(0.08, 0.04 * _count_style(styles_al, "engage"))
        E *= 1.0 + min(0.08, 0.04 * _count_style(styles_en, "engage"))
        ratio = A / E if E > 1e-6 else 3.0
        # ---- call with hysteresis
        want = self._call
        if hp is not None and hp < LOW_HP:
            want = "retreat"
        elif ratio >= ENGAGE_RATIO and (hp is None or hp >= ENGAGE_MIN_HP):
            want = "engage"
        elif ratio <= RETREAT_RATIO:
            want = "retreat"
        new_call = None
        if want is not None and want != self._call:
            if self._call_t is None or t - self._call_t >= CALL_GAP_S:
                self._call, self._call_t = want, t
                new_call = want
        safe = None
        if self._call == "retreat":
            safe = _safe_point(me_uv, vis_al, vis_en, st_map)
        reason = _reason(allies_in, enemies_in, players, me, vis_en, ref, hp)
        kind = "teamfight" if allies_in + enemies_in >= 6 else "skirmish"
        self._state = FightState(True, kind, ref, allies_in, enemies_in, round(A, 2), round(E, 2), round(ratio, 2),
                                 self._call, reason, safe, prev.since if prev.active else t, self._call_t)
        return FightUpdate(self._state, new_call, started, None, None)

    def _summary(self, game: Any, players: dict[str, Any], gt: float) -> tuple[str | None, bool | None]:
        t0 = self._start_gt
        self._start_gt = None
        if t0 is None or game is None:
            return None, None
        me = getattr(game, "me", None)
        my_team = getattr(me, "team", None)
        team_of: dict[str, str] = {}
        for p in players.values():
            for n in (getattr(p, "riot_id", ""), getattr(p, "summoner_name", "")):
                if n:
                    team_of[_norm(n)] = getattr(p, "team", "")
        ours = theirs = 0
        for ev in getattr(game, "events", None) or []:
            if not isinstance(ev, dict) or ev.get("EventName") != "ChampionKill":
                continue
            T = _f(ev.get("EventTime"))
            if T is None or T < t0 - 2.0 or T > gt + 1.0:
                continue
            victim = team_of.get(_norm(ev.get("VictimName")))
            if victim is None:
                continue
            if victim == my_team:
                theirs += 1
            else:
                ours += 1
        if ours == 0 and theirs == 0:
            return None, None
        if ours > theirs:
            return f"Combat gagné {ours} pour {theirs} !", True
        if ours == theirs:
            return f"Combat équilibré : {ours} pour {theirs}.", None
        return f"Combat perdu ({ours} pour {theirs}) : regroupe-toi avant le prochain.", False


def _count_style(aliases: list[str], style: str) -> int:
    try:
        from treeaicoach.meta import profile

        return sum(1 for a in aliases if a and profile(a).has(style))
    except Exception:
        return 0


def _avg_level(players: dict[str, Any]) -> float:
    lv = [_f(getattr(p, "level", None)) for p in players.values()]
    lv = [x for x in lv if x is not None]
    return sum(lv) / len(lv) if lv else 1.0


def _safe_point(me_uv: Any, allies: list[Seen], enemies: list[Seen], st_map: Any) -> tuple[float, float] | None:
    """Where to retreat: the nearest standing allied turret behind me (else my fountain)."""
    try:
        if st_map is not None and hasattr(st_map, "nearest_safe_uv"):
            p = st_map.nearest_safe_uv(me_uv)
            if p is not None:
                return p
    except Exception:
        pass
    if me_uv is None:
        return None
    if enemies:
        cu = sum(e.uv[0] for e in enemies) / len(enemies)
        cv = sum(e.uv[1] for e in enemies) / len(enemies)
        dx, dy = me_uv[0] - cu, me_uv[1] - cv
        n = math.hypot(dx, dy) or 1.0
        return (min(1.0, max(0.0, me_uv[0] + 0.15 * dx / n)), min(1.0, max(0.0, me_uv[1] + 0.15 * dy / n)))
    return None


def _reason(n_al: int, n_en: int, players: dict[str, Any], me: Any, vis_en: list[Seen], ref: Any,
            hp: float | None) -> str:
    parts = [f"{n_al}v{n_en}"]
    try:
        en_lv = [players[str(e.alias).lower()].level for e in vis_en
                 if e.alias and str(e.alias).lower() in players and ref is not None and geometry.dist(e.uv, ref) < FIGHT_R]
        if en_lv and me is not None:
            d = int(round(float(me.level) - sum(en_lv) / len(en_lv)))
            if d:
                parts.append(f"{'+' if d > 0 else '−'}{abs(d)} niv")
    except Exception:
        pass
    if hp is not None and hp < 0.5:
        parts.append(f"{int(round(hp * 100))} % PV")
    return " · ".join(parts)


__all__ = ["FightTracker", "FightState", "FightUpdate", "Seen", "snapshot", "champion_power",
           "CALL_WORD", "CALL_TITLE", "FIGHT_R"]
