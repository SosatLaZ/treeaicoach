"""Fight awareness + live fight calculator: one clear decision (FIGHT / RECULE) with a win chance.

During a fight the player needs ONE decision, not a stream of gank beeps. :class:`FightTracker`
runs every analysis tick (pure Python on <= 10 champions: microseconds) on the tracked minimap
icons and the public Live Client data:

* **Detection** - a fight starts when, around me (:data:`FIGHT_R`, ~2200 game units): >= 2 of us
  (me included) and >= 2 enemies are visible, or 4+ champions with at least one enemy (outside a
  plain lane 1v1 / 2v2 during the laning phase), or >= 2 enemies inside the danger radius, or a
  champion died in the last seconds next to me (``ChampionKill`` feed) with an enemy still close.
  It must hold :data:`ENTER_S`; it ends after :data:`EXIT_S` without any fight condition (or when
  I die).
* **Calculator** (:func:`evaluate`) - per side, in the engagement area: every champion counts
  ``champion_power`` = ``1 + 0.12 (level - 1) + 0.11 item_gold / 1000`` x its power curve
  (:mod:`treeaicoach.meta`: early / late champions) x its HP (mine from
  ``activePlayer.championStats``; the others are unknown: full HP, 85 % once they have been
  in this fight for a few seconds); engage champions +4 % each (max +8 %); champions OUTSIDE
  the area count if they can arrive within :data:`ARRIVE_S` (5 s) at :data:`MOVE_SPEED` - the
  visible ones from their distance, the hidden enemies from their last seen point and the time
  since (the longer hidden, the less likely); dead players count 0. Summoner spells / ultimates
  are NOT known and never guessed. ``win = 1 / (1 + ratio^-K)``.
* **Decision** - FIGHT when the win chance reaches :data:`FIGHT_ON` (60 %), RECULE when it
  drops to :data:`RETREAT_ON` (40 %) or when I am below 25 % HP; hysteresis (a decision holds
  until the chance crosses back 52 / 48 %) and at most one flip every :data:`CALL_GAP_S`.
* **Summary** - when the fight ends: kills / deaths of each side during the fight (event feed)
  -> one written line ("Combat gagné 3 pour 1 !").

Thread-safe, never raises from its public methods.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Any, Iterable

from treeaicoach import geometry

log = logging.getLogger(__name__)

FIGHT_R = 0.15                 # engagement area radius around me (normalized minimap)
DANGER_R = 0.10                # 2 enemies this close to me = I am being dived
KILL_RECENT_S = 6.0            # a death this recent (game time) next to me = fight
ENTER_S = 0.4                  # the fight condition must hold this long
EXIT_S = 4.0                   # ... and be gone this long to end the fight
CALL_GAP_S = 4.0               # min time between two decision flips
MOVE_SPEED = 0.026             # champion speed, normalized minimap units / s (~390 units/s)
ARRIVE_S = 5.0                 # champions able to join within this count
HIDDEN_MAX_S = 20.0            # hidden longer than this: position unknown, ignored
K_WIN = 3.5                    # win = 1 / (1 + ratio^-K)
FIGHT_ON, FIGHT_OFF = 0.60, 0.52
RETREAT_ON, RETREAT_OFF = 0.40, 0.48
LOW_HP = 0.25
FIGHT_MIN_HP = 0.35
WORN_HP = 0.85                 # unknown HP of a champion fighting for WORN_AFTER_S
WORN_AFTER_S = 4.0
CALL_WORD = {"engage": "Engage !", "retreat": "Recule !"}
CALL_TITLE = {"engage": "FIGHT", "retreat": "RECULE"}


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


def win_chance(ratio: float) -> float:
    """Win probability from the power ratio (allies / enemies)."""
    r = _f(ratio, 1.0) or 1.0
    if r <= 0:
        return 0.0
    return 1.0 / (1.0 + r ** (-K_WIN))


def arrival_weight(dist: float, hidden_s: float = 0.0) -> float:
    """Weight (0..1) of a champion ``dist`` away from the engagement area (hidden for ``hidden_s``)."""
    d = max(0.0, float(dist) - FIGHT_R)
    if hidden_s <= 0.0:
        return 0.75 if d <= MOVE_SPEED * ARRIVE_S else 0.0
    if hidden_s > HIDDEN_MAX_S:
        return 0.0
    # he may have walked towards us (or away) while hidden: reachable area grows with time
    if d > MOVE_SPEED * (hidden_s + ARRIVE_S):
        return 0.0
    return 0.6 * max(0.3, 1.0 - hidden_s / HIDDEN_MAX_S)


@dataclass(frozen=True)
class Seen:
    """One champion as the fight logic sees it."""

    alias: str | None
    uv: tuple[float, float] | None
    visible: bool = True
    hidden_s: float = 0.0


@dataclass(frozen=True)
class Evaluation:
    ally_power: float
    enemy_power: float
    ratio: float
    win: float                               # 0..1
    allies_in: int                           # in the engagement area (me included)
    enemies_in: int
    allies_coming: float                     # weighted champions able to join
    enemies_coming: float
    reason: str = ""


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
    win: float = 0.5
    call: str | None = None                  # "engage" | "retreat" | None
    reason: str = ""                         # short French reason ("3v2 · +2 niv")
    safe_uv: tuple[float, float] | None = None   # where to retreat (minimap arrow)
    since: float | None = None
    call_t: float | None = None

    @property
    def title(self) -> str | None:
        return CALL_TITLE.get(self.call or "")

    @property
    def win_pct(self) -> int:
        return int(round(100.0 * self.win))

    @property
    def banner(self) -> str:
        """Big banner text: "FIGHT 70 %" / "RECULE 30 %" / "COMBAT 52 %"."""
        return f"{self.title or 'COMBAT'} {self.win_pct} %"


@dataclass(frozen=True)
class FightUpdate:
    state: FightState
    new_call: str | None = None              # the decision just flipped to this ("engage" / "retreat")
    started: bool = False
    ended_summary: str | None = None         # one line when a fight just ended
    won: bool | None = None


def snapshot(tracker: Any, now: float) -> tuple[tuple[float, float] | None, list[Seen], list[Seen]]:
    """(me uv, visible allies, every enemy track) from a :class:`treeaicoach.tracker.Tracker`."""
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


def _players(game: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        for p in (game.all_players() if game is not None and hasattr(game, "all_players") else []):
            a = str(getattr(p, "champion_alias", "") or "").lower()
            if a:
                out[a] = p
    except Exception:
        pass
    return out


def _item_gold(alias: str, p: Any, scoreboard: Any) -> float:
    for line in getattr(scoreboard, "players", None) or ():
        if str(getattr(line, "alias", "")).lower() == alias:
            return float(getattr(line, "item_gold", 0) or 0)
    try:
        from treeaicoach.scoreboard import items_gold

        return float(items_gold(getattr(p, "items", None) or ()))
    except Exception:
        return 0.0


def my_hp(game: Any) -> float | None:
    stats = getattr(game, "champion_stats", None) or {}
    try:
        cur, mx = _f(stats.get("currentHealth")), _f(stats.get("maxHealth"))
    except AttributeError:
        return None
    if cur is None or not mx or mx <= 0:
        return None
    return max(0.0, min(1.0, cur / mx))


def evaluate(game: Any, centre: tuple[float, float], allies: Iterable[Seen], enemies: Iterable[Seen], *,
             scoreboard: Any = None, worn: Iterable[str] = (), gt: float | None = None) -> Evaluation:
    """The fight calculator (see the module docstring). ``centre``: engagement area centre (me);
    ``worn``: aliases (lower case) fighting long enough to have lost HP. Never raises."""
    try:
        players = _players(game)
        g = _f(gt) if gt is not None else (_f(getattr(game, "game_time", None), 0.0) or 0.0)
        worn_set = {str(w).lower() for w in worn or ()}
        dead = {a for a, p in players.items() if bool(getattr(p, "is_dead", False))}
        lv = [_f(getattr(p, "level", None)) for p in players.values()]
        lv = [x for x in lv if x is not None]
        avg_lvl = sum(lv) / len(lv) if lv else 1.0

        def power(alias: str | None, hp: float | None = None) -> float:
            a = str(alias or "").lower()
            p = players.get(a)
            if hp is None and a in worn_set:
                hp = WORN_HP
            if p is None:
                return 0.95 * champion_power(avg_lvl, 0.0, None, g or 0.0, hp)
            return champion_power(getattr(p, "level", 1), _item_gold(a, p, scoreboard), p.champion_alias,
                                  g or 0.0, hp)

        me = getattr(game, "me", None)
        me_alias = str(getattr(me, "champion_alias", "") or "")
        A = power(me_alias, my_hp(game))
        n_al, n_en = 1, 0
        come_al = come_en = 0.0
        eng_al = [me_alias]
        eng_en: list[str] = []
        for a in allies or ():
            if a.uv is None or str(a.alias or "").lower() in dead:
                continue
            d = geometry.dist(a.uv, centre)
            if d < FIGHT_R:
                A += power(a.alias)
                n_al += 1
                eng_al.append(str(a.alias or ""))
            else:
                w = arrival_weight(d)
                A += w * power(a.alias)
                come_al += w
        E = 0.0
        for e in enemies or ():
            if e.uv is None or str(e.alias or "").lower() in dead:
                continue
            d = geometry.dist(e.uv, centre)
            if e.visible and d < FIGHT_R:
                E += power(e.alias)
                n_en += 1
                eng_en.append(str(e.alias or ""))
            else:
                w = arrival_weight(d, 0.0 if e.visible else max(0.01, e.hidden_s))
                E += w * power(e.alias)
                come_en += w
        A *= 1.0 + min(0.08, 0.04 * _count_style(eng_al, "engage"))
        E *= 1.0 + min(0.08, 0.04 * _count_style(eng_en, "engage"))
        ratio = A / E if E > 1e-6 else 3.0
        return Evaluation(round(A, 3), round(E, 3), round(ratio, 3), round(win_chance(ratio), 3), n_al, n_en,
                          round(come_al, 2), round(come_en, 2),
                          _reason(n_al, n_en, come_en, players, me, eng_en, my_hp(game)))
    except Exception:
        log.debug("fight.evaluate failed", exc_info=True)
        return Evaluation(1.0, 1.0, 1.0, 0.5, 1, 0, 0.0, 0.0, "")


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
            self._in_since: dict[str, float] = {}

    def state(self) -> FightState:
        with self._lock:
            return self._state

    def in_fight(self) -> bool:
        with self._lock:
            return self._state.active

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

    def _recent_kill_near(self, game: Any, gt: float, me_uv: Any, allies: list[Seen],
                          players: dict[str, Any]) -> bool:
        names_near: set[str] = set()
        me = getattr(game, "me", None)
        if me is not None:
            names_near |= {_norm(me.riot_id), _norm(me.summoner_name)}
        for a in allies:
            if a.alias and a.uv is not None and me_uv is not None and geometry.dist(a.uv, me_uv) < FIGHT_R:
                p = players.get(str(a.alias).lower())
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
        players = _players(game)
        dead_aliases = {a for a, p in players.items() if bool(getattr(p, "is_dead", False))}
        vis_en = [e for e in enemies if e.visible and e.uv is not None
                  and str(e.alias or "").lower() not in dead_aliases]
        vis_al = [a for a in allies if a.uv is not None and str(a.alias or "").lower() not in dead_aliases]
        cond = False
        near_en: list[Seen] = []
        near_al: list[Seen] = []
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
            non_lane = [e for e in near_en if str(e.alias or "").lower() not in lane_opps]
            if not in_base:
                if n_al >= 2 and n_en >= 2 and (not laning or non_lane or n_al + n_en >= 5):
                    cond = True
                elif n_al + n_en >= 4 and n_en >= 1 and (non_lane or not laning):
                    cond = True
                elif len(in_danger) >= 2 and n_al >= 2:
                    cond = True                     # dived next to an ally (alone: it is a gank)
                elif n_en >= 1 and self._recent_kill_near(game, gt, me_uv, vis_al, players):
                    cond = True
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
            self._call, self._call_t = None, None
            self._in_since = {}
        ended_summary = None
        won = None
        if active and (dead or self._last_cond is None or t - self._last_cond >= EXIT_S):
            active = False
            ended_summary, won = self._summary(game, players, gt)
            self._call, self._call_t = None, None
            self._in_since = {}
        if not active or me_uv is None:
            self._state = FightState(active=False)
            return FightUpdate(self._state, None, False, ended_summary, won)
        # ---- calculator
        for s in near_en + near_al:
            if s.alias:
                self._in_since.setdefault(str(s.alias).lower(), t)
        worn = [a for a, t0 in self._in_since.items() if t - t0 >= WORN_AFTER_S]
        ev = evaluate(game, me_uv, vis_al, enemies, scoreboard=scoreboard, worn=worn, gt=gt)
        hp = my_hp(game)
        want = self._call
        if hp is not None and hp < LOW_HP:
            want = "retreat"
        elif self._call == "engage":
            if ev.win < FIGHT_OFF:
                want = "retreat" if ev.win <= RETREAT_ON else None
        elif self._call == "retreat":
            if ev.win > RETREAT_OFF:
                want = "engage" if ev.win >= FIGHT_ON and (hp is None or hp >= FIGHT_MIN_HP) else None
        else:
            if ev.win >= FIGHT_ON and (hp is None or hp >= FIGHT_MIN_HP):
                want = "engage"
            elif ev.win <= RETREAT_ON:
                want = "retreat"
        new_call = None
        if want != self._call and (self._call_t is None or t - self._call_t >= CALL_GAP_S):
            self._call, self._call_t = want, t
            new_call = want
        safe = _safe_point(me_uv, vis_en, st_map) if self._call == "retreat" else None
        kind = "teamfight" if ev.allies_in + ev.enemies_in >= 6 else "skirmish"
        self._state = FightState(True, kind, me_uv, ev.allies_in, ev.enemies_in, ev.ally_power, ev.enemy_power,
                                 ev.ratio, ev.win, self._call, ev.reason, safe,
                                 prev.since if prev.active else t, self._call_t)
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


def _safe_point(me_uv: Any, enemies: list[Seen], st_map: Any) -> tuple[float, float] | None:
    """Where to retreat: the nearest standing allied turret behind me (else away from the enemies)."""
    try:
        if st_map is not None and hasattr(st_map, "nearest_safe_uv"):
            p = st_map.nearest_safe_uv(me_uv)
            if p is not None:
                return p
    except Exception:
        pass
    if me_uv is None:
        return None
    pts = [e.uv for e in enemies if e.uv is not None]
    if pts:
        cu = sum(p[0] for p in pts) / len(pts)
        cv = sum(p[1] for p in pts) / len(pts)
        dx, dy = me_uv[0] - cu, me_uv[1] - cv
        n = math.hypot(dx, dy) or 1.0
        return (min(1.0, max(0.0, me_uv[0] + 0.15 * dx / n)), min(1.0, max(0.0, me_uv[1] + 0.15 * dy / n)))
    return None


def _reason(n_al: int, n_en: int, coming: float, players: dict[str, Any], me: Any, eng_en: list[str],
            hp: float | None) -> str:
    parts = [f"{n_al}v{n_en}"]
    if coming >= 0.4:
        n = max(1, int(round(coming)))
        parts.append(f"+{n} ennemi{'s' if n > 1 else ''} en route")
    try:
        en_lv = [players[a.lower()].level for a in eng_en if a and a.lower() in players]
        if en_lv and me is not None:
            d = int(round(float(me.level) - sum(en_lv) / len(en_lv)))
            if d:
                parts.append(f"{'+' if d > 0 else '−'}{abs(d)} niv")
    except Exception:
        pass
    if hp is not None and hp < 0.5:
        parts.append(f"{int(round(hp * 100))} % PV")
    return " · ".join(parts)


__all__ = ["FightTracker", "FightState", "FightUpdate", "Seen", "snapshot", "champion_power", "evaluate",
           "Evaluation", "win_chance", "arrival_weight", "my_hp", "CALL_WORD", "CALL_TITLE", "FIGHT_R"]
