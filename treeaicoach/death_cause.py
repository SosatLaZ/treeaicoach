"""Why did I die? One written line, live, from the map state just before my death.

:func:`classify_death` is a pure function on a small snapshot (what the coach knew a few seconds
before the death + the Live Client ``ChampionKill`` event) and returns ``(cause, line)``:

=================  ==========================================================================
``tower``          killed by a turret: "Ne frappe pas leur tour seul : tué par la tour"
``outnumbered``    more enemies than allies around me: "Mort à 3 contre 1 : recule dès qu'ils sont plus nombreux"
``jungler``        the enemy jungler took part and was unseen for a while: "Leur jungler t'a surpris : ..."
``outlevelled``    the killer had 2+ levels more: "Darius avait 2 niveaux de plus : évite ses échanges"
``low_hp``         I stayed low: "Tu es resté à 25 % de vie : rentre plus tôt"
``overextended``   deep in the enemy half with several enemies missing: "Trop avancé avec 3 ennemis invisibles : ..."
=================  ==========================================================================

or None when nothing clear stands out (no line rather than a guess). :class:`DeathCoach` keeps a
short history of snapshots and produces the line once per death (shown as a toast + HUD line by
the engine, never spoken). Reusable by other modules (play ratings: "GAFFE ??" detection).
Only the minimap facts of the coach + the official Live Client API. Pure Python, never raises.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

SNAP_BEFORE_S = 4.0         # the map state this long before the death is used (positions at death are gone)
EARLY_HP_S = 10.0           # "you stayed low" needs low health this long before too (not just the fight)
DIVE_MIN = 4                # this many enemies on me on my side of the map: a dive, not my mistake
LOW_HP = 0.35
JUNGLER_SURPRISE_S = 15.0   # jungler unseen at least this long before he killed me
CAUSE_DELAY_S = 0.0         # the lesson is shown at once (the card would otherwise blink empty, then fill)


@dataclass(frozen=True)
class DeathSnapshot:
    """What was known just before the death (all optional, defaults = unknown)."""

    gt: float = 0.0
    enemies_near: int = 0              # visible enemies around me (coach facts "numbers")
    allies_near: int = 1               # me included
    involved: int = 0                  # enemy champions in the kill event (killer + assisters)
    killer_kind: str = "champion"      # "champion" | "turret" | "monster" | "unknown"
    killer_name: str = ""
    killer_level_diff: int = 0         # my level - killer's level
    jungler_involved: bool = False
    jungler_hidden_s: float | None = None
    hp: float | None = None            # my health 0..1 a few seconds before
    hp_early: float | None = None      # my health ~10 s before (was I ALREADY low before the fight?)
    enemy_half: bool = False           # I was in the enemy half of the map
    missing: int = 0                   # enemies hidden after being seen recently


def classify_death(s: DeathSnapshot) -> tuple[str, str] | None:
    """``(cause, French line)`` or None (see the module docstring). Pure, never raises."""
    try:
        if s.killer_kind == "turret" and s.involved <= 1:
            return "tower", "Ne frappe pas leur tour seul : tué par la tour"
        n = max(int(s.enemies_near), int(s.involved))
        al = max(1, int(s.allies_near))
        # V2 audit: a 4-5 man dive on my side is not a positioning mistake: no blame, one hint
        if n >= DIVE_MIN and not s.enemy_half and n > al:
            return "dive", f"Recule dès qu'ils disparaissent : plongée à {n} contre {al}"
        # the jungler gank is the precise cause (before "2 contre 1", which is the same death)
        if s.jungler_involved and (s.jungler_hidden_s is None or s.jungler_hidden_s >= JUNGLER_SURPRISE_S):
            if s.enemy_half:
                return "jungler", "Reste près de ta tour : leur jungler t'a surpris"
            return "jungler", "Balise ta rivière quand tu avances : leur jungler t'a eu"
        if n >= 2 and n > al:
            return "outnumbered", f"Recule vers ta tour plus tôt : mort à {n} contre {al}"
        if s.killer_level_diff <= -2 and s.killer_name:
            return "outlevelled", f"Évite les échanges avec {s.killer_name} : {-s.killer_level_diff} niveaux de plus"
        if s.hp is not None and s.hp < LOW_HP and (s.hp_early is None or s.hp_early < LOW_HP):
            return "low_hp", f"Rentre plus tôt en base : mort à {int(round(100 * s.hp / 5.0) * 5)} % de vie"
        if s.enemy_half and s.missing >= 2:
            return "overextended", f"Recule quand ils disparaissent : {s.missing} ennemis invisibles"
        return None
    except Exception:
        log.debug("classify_death failed", exc_info=True)
        return None


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _names(p: Any) -> set[str]:
    out = set()
    for attr in ("riot_id", "summoner_name"):
        v = str(getattr(p, attr, "") or "").strip().casefold()
        if v:
            out.add(v)
            out.add(v.split("#", 1)[0])
    return out


def _hp(game: Any) -> float | None:
    st = getattr(game, "champion_stats", None) or {}
    cur, mx = _f(st.get("currentHealth")), _f(st.get("maxHealth"))
    return max(0.0, min(1.0, cur / mx)) if cur is not None and mx else None


def _enemy_half(pos: Any, team: str | None) -> bool:
    if pos is None or team not in ("ORDER", "CHAOS"):
        return False
    u, v = float(pos[0]), float(pos[1])
    # the river is the u = v diagonal: blue (ORDER) base bottom-left (u - v = -1), red top-right (+1)
    d = u - v
    return d > 0.12 if team == "ORDER" else d < -0.12


def snapshot_from(facts: dict, game: Any) -> dict[str, Any]:
    """The per-tick part of a snapshot (kept in :class:`DeathCoach`'s history)."""
    f = facts or {}
    nums = f.get("numbers") or (0, 1)
    jg = f.get("jungler") or {}
    return {"gt": _f(f.get("gt")) or _f(getattr(game, "game_time", 0.0)) or 0.0,
            "enemies_near": int(_f(nums[0]) or 0), "allies_near": max(1, int(_f(nums[1]) or 1)),
            "jungler_hidden_s": _f(jg.get("hidden_s")) if not jg.get("visible") else 0.0,
            "jungler_alias": jg.get("alias"), "hp": _hp(game),
            "enemy_half": _enemy_half(f.get("me_pos"), getattr(game, "my_team", None)),
            "missing": int(_f(f.get("missing")) or 0)}


class DeathCoach:
    """Snapshots while alive, one cause line per death. Owned by CoachPlus (not thread-safe)."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._hist: deque = deque(maxlen=40)
        self._was_dead = False
        self._pending: tuple[float, tuple[str, str]] | None = None
        self.last: tuple[str, str] | None = None
        self.causes: list[str] = []

    def update(self, gt: float, facts: dict, game: Any) -> tuple[str, str] | None:
        """``(cause, line)`` when it is time to show it (once per death), else None."""
        me = getattr(game, "me", None)
        if me is None:
            return None
        dead = bool(getattr(me, "is_dead", False))
        if not dead:
            self._was_dead = False
            if facts:
                self._hist.append(snapshot_from(facts, game))
            self._pending = None
            return None
        if not self._was_dead:
            self._was_dead = True
            res = classify_death(self._build(gt, game))
            self.last = res
            if res is not None:
                self.causes.append(res[0])
                self._pending = (gt + CAUSE_DELAY_S, res)
        if self._pending is not None and gt >= self._pending[0]:
            res, self._pending = self._pending[1], None
            return res
        return None

    def _build(self, gt: float, game: Any) -> DeathSnapshot:
        snap: dict[str, Any] = {}
        for s in reversed(self._hist):
            if s["gt"] <= gt - SNAP_BEFORE_S or not snap:
                snap = s
                if s["gt"] <= gt - SNAP_BEFORE_S:
                    break
        early = None
        for s in reversed(self._hist):
            if s["gt"] <= gt - EARLY_HP_S:
                early = s
                break
        me = game.me
        names = _names(me)
        ev = None
        for e in reversed(getattr(game, "events", None) or []):
            if isinstance(e, dict) and e.get("EventName") == "ChampionKill" and \
                    str(e.get("VictimName") or "").strip().casefold() in names and \
                    (_f(e.get("EventTime")) or 0.0) >= gt - 15.0:
                ev = e
                break
        players = {}
        for p in getattr(game, "enemies", None) or []:
            for n in _names(p):
                players[n] = p
        involved, kind, kname, kdiff, jg_in = 0, "unknown", "", 0, False
        jg_alias = str(snap.get("jungler_alias") or "").lower()
        jg = game.enemy_jungler() if hasattr(game, "enemy_jungler") else None
        if not jg_alias and jg is not None:
            jg_alias = str(jg.champion_alias).lower()
        if ev is not None:
            killer = str(ev.get("KillerName") or "").strip().casefold()
            kp = players.get(killer)
            if kp is not None:
                kind, kname = "champion", str(kp.champion_name or kp.champion_alias)
                kdiff = int(getattr(me, "level", 1)) - int(getattr(kp, "level", 1))
            elif "turret" in killer or "tour" in killer:
                kind = "turret"
            elif killer:
                kind = "monster" if not killer.startswith("minion") else "minion"
            who = [kp] if kp is not None else []
            who += [players.get(str(a).strip().casefold()) for a in ev.get("Assisters") or []]
            who = [p for p in who if p is not None]
            involved = len(who)
            jg_in = any(str(p.champion_alias).lower() == jg_alias for p in who) if jg_alias else False
        return DeathSnapshot(
            gt=gt, enemies_near=int(snap.get("enemies_near", 0)), allies_near=int(snap.get("allies_near", 1)),
            involved=involved, killer_kind=kind, killer_name=kname, killer_level_diff=kdiff,
            jungler_involved=jg_in, jungler_hidden_s=snap.get("jungler_hidden_s"),
            hp=snap.get("hp"), hp_early=early.get("hp") if early is not None else None,
            enemy_half=bool(snap.get("enemy_half")), missing=int(snap.get("missing", 0)))


__all__ = ["DeathSnapshot", "DeathCoach", "classify_death", "snapshot_from"]
