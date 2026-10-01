"""Personal danger: am I about to die to the enemies next to me? (gank or not, on screen or not)

The gank analyser (:mod:`treeaicoach.gank`) only announces an enemy jungler / roamer COMING at
me, never my lane opponent, and the engine writes (instead of saying) a gank warning whose
enemies are already on my screen. The first real game report (Garen top, 15 deaths, 11 "sans
alerte") showed what that leaves out: a beginner dies 1v1 to the lane opponent standing next to
him (Vladimir at 2:37, 12:59, 15:00), or with the enemy jungler visible on screen for 7 s. This
module looks at ME instead of at the gank: my HP / level / items (Live Client) against the
visible enemies close to me (their level and item gold, how many, coming closer or not) and
produces at most one :class:`~treeaicoach.alerts.Alert` of kind ``PERSONAL_DANGER`` per tick:

=====================  ========  ==============================================================
rule                    level     condition (``hp`` = my health share)
=====================  ========  ==============================================================
``recule``              DANGER    spoken "Recule !": ``hp <= 0.35`` and an enemy on me (within
                                  :data:`CLOSE_R`, or within :data:`NEAR_R` and coming closer),
                                  or ``hp <= 0.55`` and outnumbered (2+ enemies near, more than
                                  my allies + 1); not when I am clearly the stronger one
``lane``                WARNING   written: my lane opponent near me with a level / item spike
                                  (2+ levels, 6 vs 5, 900+ gold of items, power x1.3) while I am
                                  hurt (< 70 %) or far behind: "Vladimir te domine : ne trade
                                  pas, farme sous la tour."
``low``                 WARNING   written: ``hp < 0.4`` and an enemy near: "Peu de vie et
                                  Vladimir près de toi : recule."
``outnumbered``         WARNING   written: 2+ enemies near and more than my allies + 1 (when no
                                  gank alert covers it): "3 ennemis près de toi : recule vers ta
                                  tour."
``jungler_fog``         WARNING   written: the enemy jungler unseen for 8-60 s, the fog model
                                  (``FogEstimate.heat`` / region, jungle_path.py) puts >= 35 % of
                                  his probable position within ~7 s of walking from me, and I
                                  stand past the middle of the map (my lane pushed / enemy half):
                                  "Kindred peut arriver : recule vers ta tour."
=====================  ========  ==============================================================

Not spammy: one message per tick, "Recule !" never twice within :data:`RECULE_REPEAT_S` (20 s),
the same written line never within :data:`SAME_TEXT_S`, per-rule cooldowns, nothing in my
fountain / while dead / during a fight (the fight call speaks) / right after a gank DANGER
("Gank ! ..., recule !" already said it). Pure Python (numpy for the fog part), thread-confined
to the analysis thread, never raises from :meth:`PersonalDanger.update`.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

from treeaicoach.alerts import Alert, AlertKind, Level, alert_key
from treeaicoach.geometry import dist, in_fountain, normalize_team

log = logging.getLogger(__name__)

# distances in normalized minimap units (1 = 14870 game units; my screen is ~0.13 x 0.075)
CLOSE_R = 0.09              # on me (on my screen, < ~1350 units)
NEAR_R = 0.12               # near (a few seconds away)
LANE_R = 0.15               # my lane opponent "in front of me"
ALLY_R = 0.10               # allies with me
APPROACH_DROP = 0.012       # distance drop over APPROACH_WINDOW_S = "coming closer"
APPROACH_WINDOW_S = 1.2
POS_MAX_AGE_S = 3.0         # my position unknown for longer -> nothing
ENEMY_MAX_AGE_S = 1.0       # an enemy icon not seen for this long is not "visible near me"
# HP thresholds (share of max health)
RECULE_HP = 0.35
RECULE_OUTNUMBERED_HP = 0.55
LOW_HP = 0.4
LANE_HURT_HP = 0.7
OUTNUMBERED_HP = 0.8
STRONGER_SKIP = 2.0         # my power / theirs >= this: I am clearly the stronger one, no "Recule"
# lane spike
LANE_LEVEL_GAP = 2
LANE_GOLD_GAP = 900
LANE_POWER_RATIO = 1.3
LANE_FAR_BEHIND_RATIO = 1.5  # this far behind: warned even at full HP
# anti-spam
RECULE_REPEAT_S = 20.0
SAME_TEXT_S = 20.0
RULE_COOLDOWN_S = {"lane": 90.0, "low": 25.0, "outnumbered": 25.0, "jungler_fog": 45.0}
WRITTEN_GAP_S = 8.0         # two written danger lines at least this far apart
AFTER_RECULE_S = 10.0       # no written "low HP" / "outnumbered" echo right after "Recule !"
AFTER_GANK_DANGER_S = 4.0   # a gank DANGER ("..., recule !") was raised this recently: no "Recule !"
# jungler in the fog
FOG_MIN_HIDDEN_S = 8.0
FOG_MAX_HIDDEN_S = 60.0
FOG_ETA_S = 7.0
FOG_MIN_MASS = 0.35
FOG_SPEED = 390.0 / 14870.0  # boots speed (normalized / s), as gank.ETA_REF_SPEED
FOG_FLASH = 0.027
PAST_MID_MARGIN = 0.05      # past the river diagonal (u - v) by this much = "past mid-lane"


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def _key(s: Any) -> str:
    return "".join(ch for ch in str(s or "") if ch.isalnum()).casefold()


def my_hp(game: Any) -> float | None:
    """My health share 0..1 from the Live Client ``championStats`` (None when unknown)."""
    st = getattr(game, "champion_stats", None) or {}
    try:
        cur, mx = _f(st.get("currentHealth")), _f(st.get("maxHealth"))
    except AttributeError:
        return None
    if cur is None or not mx or mx <= 0:
        return None
    return max(0.0, min(1.0, cur / mx))


def past_mid(uv: tuple[float, float] | None, team: str | None) -> bool:
    """I stand past the middle of the map (the river diagonal) towards the enemy base."""
    if uv is None or team not in ("ORDER", "CHAOS"):
        return False
    d = float(uv[0]) - float(uv[1])            # -1 at the blue (ORDER) base, +1 at the red one
    return d > -PAST_MID_MARGIN if team == "ORDER" else d < PAST_MID_MARGIN


def _gold(items: Iterable[Any]) -> int:
    try:
        from treeaicoach.scoreboard import items_gold

        return int(items_gold(items or ()))
    except Exception:
        return 0


def _power(level: Any, gold: float, alias: str | None, gt: float, hp: float | None = None) -> float:
    try:
        from treeaicoach.fight import champion_power

        return float(champion_power(level, gold, alias, gt, hp))
    except Exception:
        lvl = _f(level) or 1.0
        p = 1.0 + 0.12 * (lvl - 1.0) + 0.11 * gold / 1000.0
        return p * (0.3 + 0.7 * hp) if hp is not None else p


@dataclass(frozen=True)
class Foe:
    """A visible enemy around me (snapshot used by the rules and the tests)."""

    key: str
    alias: str | None
    name: str
    d: float
    approaching: bool
    level: int = 0
    gold: int = 0
    power: float = 1.0
    lane_opponent: bool = False


@dataclass(frozen=True)
class DangerState:
    """Immutable snapshot of the last evaluation (UI / tests)."""

    t: float | None = None
    hp: float | None = None
    foes: tuple[Foe, ...] = ()
    allies_near: int = 0
    rule: str | None = None             # rule of the last message produced
    jungler_fog_mass: float | None = None
    suppressed: str | None = None


@dataclass
class _Memory:
    dists: dict[str, deque] = field(default_factory=dict)
    rule_t: dict[str, float] = field(default_factory=dict)
    text_t: dict[str, float] = field(default_factory=dict)
    recule_t: float = -math.inf
    written_t: float = -math.inf
    lane_level_gap: dict[str, int] = field(default_factory=dict)


class PersonalDanger:
    """See the module docstring. One instance per game (``reset()`` on a new game)."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._m = _Memory()
        self._state = DangerState()
        self._last_t: float | None = None
        self._reach: Any = None
        self._reach_failed = False

    def state(self) -> DangerState:
        return self._state

    # ------------------------------------------------------------------ public
    def update(self, t: float, gt: float, game: Any, tracker: Any, *, lane_opponents: Iterable[str] = (),
               jungler: str | None = None, threat: int = 0, gank_danger_t: float | None = None,
               in_fight: bool = False, fog: Iterable[Any] = ()) -> list[Alert]:
        """At most one alert for time ``t`` (engine clock), ``gt`` = game time. Never raises.

        ``lane_opponents``: aliases of my lane opponent(s) (roles); ``jungler``: alias of the
        enemy jungler; ``threat``: current gank threat level (0 / 1 / 2); ``gank_danger_t``: engine
        time of the last gank DANGER alert; ``fog``: :class:`treeaicoach.fog_tracker.FogEstimate`
        list of the last tick."""
        try:
            return self._update(float(t), float(gt or 0.0), game, tracker, lane_opponents, jungler,
                                int(threat or 0), gank_danger_t, bool(in_fight), fog)
        except Exception:
            log.exception("PersonalDanger.update failed")
            return []

    # ------------------------------------------------------------------ internals
    def _quiet(self, t: float, why: str, hp: float | None = None) -> list[Alert]:
        self._m.dists.clear()
        self._state = DangerState(t=t, hp=hp, suppressed=why)
        return []

    def _update(self, t: float, gt: float, game: Any, tracker: Any, lane_opponents: Iterable[str],
                jungler: str | None, threat: int, gank_danger_t: float | None, in_fight: bool,
                fog: Iterable[Any]) -> list[Alert]:
        if self._last_t is not None and t < self._last_t - 1.0:
            self.reset()                                   # new timeline
        self._last_t = t
        me_info = getattr(game, "me", None) if game is not None else None
        if me_info is None or tracker is None:
            return self._quiet(t, "no_game")
        if not bool(getattr(game, "is_summoners_rift", True)):
            return self._quiet(t, "mode")
        hp = my_hp(game)
        if bool(getattr(me_info, "is_dead", False)):
            return self._quiet(t, "dead", hp)
        me = tracker.me()
        me_pos = me.position() if me is not None else None
        if me is None or me_pos is None or t - float(getattr(me, "last_seen", t)) > POS_MAX_AGE_S:
            return self._quiet(t, "unknown_position", hp)
        team = normalize_team(getattr(me_info, "team", None)) or normalize_team(getattr(me, "team", None))
        if in_fountain(me_pos[0], me_pos[1], team):
            return self._quiet(t, "fountain", hp)

        lane_keys = {_key(a) for a in lane_opponents or ()} - {""}
        foes = self._foes(t, gt, game, tracker, me_pos, lane_keys)
        allies_near = 0
        try:
            for a in tracker.allies(visible_only=True):
                p = a.position()
                if p is not None and getattr(a, "key", None) != getattr(me, "key", None) \
                        and dist(p, me_pos) < ALLY_R and getattr(a, "alias", None):
                    allies_near += 1
        except Exception:
            allies_near = 0
        my_level = int(_f(getattr(me_info, "level", 1)) or 1)
        my_gold = _gold(getattr(me_info, "items", None) or ())
        mine = _power(my_level, my_gold, getattr(me_info, "champion_alias", None), gt, hp)
        near = [f for f in foes if f.d < NEAR_R]
        on_me = [f for f in foes if f.d < CLOSE_R or (f.d < NEAR_R and f.approaching)]
        theirs = sum(f.power for f in near) or 0.0
        recent_gank = gank_danger_t is not None and 0.0 <= t - gank_danger_t < AFTER_GANK_DANGER_S
        fog_mass = None

        out: Alert | None = None
        rule: str | None = None
        # ---- 1) spoken "Recule !"
        outnumbered = len(near) >= 2 and len(near) > 1 + allies_near
        if hp is not None and not in_fight and not recent_gank and threat < Level.DANGER:
            stronger = theirs > 0 and mine / theirs >= STRONGER_SKIP
            if ((hp <= RECULE_HP and on_me) or (hp <= RECULE_OUTNUMBERED_HP and outnumbered)) and not stronger:
                if t - self._m.recule_t >= RECULE_REPEAT_S:
                    who = (on_me or near)[0]
                    out = Alert(kind=AlertKind.PERSONAL_DANGER, level=Level.DANGER, text="Recule !",
                                key=alert_key(AlertKind.PERSONAL_DANGER, "recule"), t=t, alias=who.alias)
                    rule = "recule"
        # ---- 2) written warnings (one per tick, cooldowns)
        if out is None and not in_fight:
            cands: list[tuple[str, str, str | None]] = []
            lane = self._lane_spike(near_lane=[f for f in foes if f.lane_opponent and f.d < LANE_R],
                                    hp=hp, my_level=my_level, my_gold=my_gold, mine=mine)
            if lane is not None:
                cands.append(lane)
            just_said = t - self._m.recule_t < AFTER_RECULE_S     # "Recule !" said: no written echo
            if hp is not None and hp < LOW_HP and near and threat < Level.WARNING and not just_said:
                f0 = min(near, key=lambda f: f.d)
                cands.append(("low", f"Peu de vie et {f0.name} près de toi : recule.", f0.alias))
            if outnumbered and threat < Level.WARNING and (hp is None or hp < OUTNUMBERED_HP) and not just_said:
                cands.append(("outnumbered", f"{len(near)} ennemis près de toi : recule vers ta tour.", None))
            if threat < Level.WARNING and not near:
                fog_mass, jg = self._jungler_fog(me_pos, team, jungler, fog)
                if jg is not None:
                    cands.append(("jungler_fog", f"{jg} peut arriver : recule vers ta tour.", None))
            for r, text, alias in cands:
                if self._written_ok(r, text, t):
                    out = Alert(kind=AlertKind.PERSONAL_DANGER, level=Level.WARNING, text=text,
                                key=alert_key(AlertKind.PERSONAL_DANGER, r if alias is None else f"{r}:{alias}"),
                                t=t, alias=alias)
                    rule = r
                    break
        if out is not None:
            if rule == "recule":
                self._m.recule_t = t
            else:
                self._m.written_t = t
                self._m.rule_t[rule or ""] = t
            self._m.text_t[out.text] = t
        self._state = DangerState(t=t, hp=hp, foes=tuple(foes), allies_near=allies_near, rule=rule,
                                  jungler_fog_mass=fog_mass)
        return [out] if out is not None else []

    def _written_ok(self, rule: str, text: str, t: float) -> bool:
        if t - self._m.written_t < WRITTEN_GAP_S:
            return False
        last = self._m.rule_t.get(rule)
        if last is not None and t - last < RULE_COOLDOWN_S.get(rule, 30.0):
            return False
        seen = self._m.text_t.get(text)
        return seen is None or t - seen >= SAME_TEXT_S

    def _foes(self, t: float, gt: float, game: Any, tracker: Any, me_pos: tuple[float, float],
              lane_keys: set[str]) -> list[Foe]:
        dead = set()
        for p in getattr(game, "enemies", None) or ():
            if bool(getattr(p, "is_dead", False)):
                dead.add(_key(getattr(p, "champion_alias", "")))
        out: list[Foe] = []
        seen: set[str] = set()
        for tr in tracker.enemies(visible_only=True):
            pos = tr.position()
            if pos is None or t - float(getattr(tr, "last_seen", t)) > ENEMY_MAX_AGE_S:
                continue
            alias = getattr(tr, "alias", None) or None
            if alias and _key(alias) in dead:
                continue
            d = dist(me_pos, pos)
            key = str(getattr(tr, "key", alias or ""))
            seen.add(key)
            hist = self._m.dists.setdefault(key, deque(maxlen=24))
            if not hist or hist[-1][0] < t:
                hist.append((t, d))
            old = next((x for x in hist if t - x[0] <= APPROACH_WINDOW_S), None)
            approaching = old is not None and t - old[0] >= 0.4 and old[1] - d >= APPROACH_DROP
            if d >= max(NEAR_R, LANE_R) * 1.5:
                continue
            p = None
            if alias and hasattr(game, "player_by_alias"):
                try:
                    p = game.player_by_alias(alias)
                except Exception:
                    p = None
            name = str(getattr(p, "champion_name", "") or alias or "un ennemi")
            level = int(_f(getattr(p, "level", 0)) or 0) if p is not None else 0
            gold = _gold(getattr(p, "items", None) or ()) if p is not None else 0
            power = _power(level or 1, gold, alias, gt) if p is not None else 1.0
            out.append(Foe(key=key, alias=alias, name=name, d=round(d, 4), approaching=approaching, level=level,
                           gold=gold, power=power, lane_opponent=bool(alias and _key(alias) in lane_keys)))
        for k in [k for k in self._m.dists if k not in seen]:
            del self._m.dists[k]
        out.sort(key=lambda f: f.d)
        return out

    def _lane_spike(self, near_lane: list[Foe], hp: float | None, my_level: int, my_gold: int,
                    mine: float) -> tuple[str, str, str | None] | None:
        """My lane opponent in front of me with a level / item spike while I am hurt."""
        for f in near_lane:
            if f.level <= 0:
                continue
            gap = f.level - my_level
            spike = (gap >= LANE_LEVEL_GAP or (f.level >= 6 > my_level) or f.gold - my_gold >= LANE_GOLD_GAP)
            mine_full = mine if hp is None else mine / (0.3 + 0.7 * hp)      # power at full HP
            ratio = f.power / mine_full if mine_full > 0 else 1.0
            spike = spike or ratio >= LANE_POWER_RATIO
            hurt = hp is not None and hp < LANE_HURT_HP
            if spike and (hurt or ratio >= LANE_FAR_BEHIND_RATIO):
                return ("lane", f"{f.name} te domine : ne trade pas, farme sous la tour.", f.alias)
        return None

    # ------------------------------------------------------------------ jungler in the fog
    def _get_reach(self) -> Any:
        if self._reach is None and not self._reach_failed:
            try:
                from treeaicoach.fog_tracker import shared_reachability

                self._reach = shared_reachability()
            except Exception:
                log.debug("Walkable mask unavailable: no fog danger", exc_info=True)
                self._reach_failed = True
        return self._reach

    def _jungler_fog(self, me_pos: tuple[float, float], team: str | None, jungler: str | None,
                     fog: Iterable[Any]) -> tuple[float | None, str | None]:
        """``(probability mass within ~7 s of me, jungler name or None when not worth a warning)``."""
        if not past_mid(me_pos, team):
            return None, None
        est = None
        jk = _key(jungler)
        for e in fog or ():
            if bool(getattr(e, "is_jungler", False)) or (jk and _key(getattr(e, "alias", "")) == jk):
                est = e
                break
        if est is None:
            return None, None
        elapsed = _f(getattr(est, "elapsed", None))
        if elapsed is None or not (FOG_MIN_HIDDEN_S <= elapsed <= FOG_MAX_HIDDEN_S):
            return None, None
        mass = fog_mass_near(est, me_pos, FOG_ETA_S, self._get_reach())
        if mass is None or mass < FOG_MIN_MASS:
            return mass, None
        name = str(getattr(est, "name", "") or getattr(est, "alias", "") or "Le jungler ennemi")
        return mass, name


def fog_mass_near(est: Any, me_pos: tuple[float, float], eta_s: float, reach: Any = None) -> float | None:
    """Share of the fog estimate's probable position (``heat``, else uniform over ``region``,
    else the straight-line circle) from which the enemy reaches me within ``eta_s`` seconds of
    walking (+ a Flash). None when unknown. Never raises."""
    try:
        import numpy as np

        reach_d = FOG_FLASH + eta_s * FOG_SPEED
        heat = getattr(est, "heat", None)
        region = getattr(est, "region", None)
        grid = None
        if heat is not None:
            grid = np.asarray(heat, dtype=np.float64)
        elif region is not None:
            grid = np.asarray(region, dtype=np.float64)
        if grid is None or grid.ndim != 2 or grid.size == 0 or not np.isfinite(grid).all() or grid.sum() <= 0:
            last = getattr(est, "last_uv", None)
            r = _f(getattr(est, "radius", None))
            if last is None or r is None or r <= 0:
                return None
            # straight line: share of the disc of radius r within reach_d of me (area ratio, crude)
            d = dist(me_pos, (float(last[0]), float(last[1])))
            if d >= r + reach_d:
                return 0.0
            return 1.0 if reach_d >= r else float((reach_d / r) ** 2)
        h, w = grid.shape
        if reach is not None:
            fld = reach.distance_field(me_pos, max_dist=reach_d + 0.02)
            fld = np.asarray(fld, dtype=np.float64)
            g = fld.shape[0]
            if (h, w) != fld.shape:
                rows = (np.arange(h) * g // h).clip(0, g - 1)
                cols = (np.arange(w) * g // w).clip(0, g - 1)
                fld = fld[np.ix_(rows, cols)]
        else:
            cu = (np.arange(w) + 0.5) / w
            cv = (np.arange(h) + 0.5) / h
            fld = np.hypot(cu[None, :] - me_pos[0], cv[:, None] - me_pos[1])
        total = float(grid.sum())
        near = float(grid[fld <= reach_d].sum())
        return near / total if total > 0 else None
    except Exception:
        log.debug("fog_mass_near failed", exc_info=True)
        return None


__all__ = ["PersonalDanger", "DangerState", "Foe", "fog_mass_near", "my_hp", "past_mid",
           "CLOSE_R", "NEAR_R", "RECULE_HP", "RECULE_REPEAT_S"]
