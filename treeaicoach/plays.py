"""Play classification ("coups" à la chess.com): rate the player's key moments of a game.

Like a chess engine marks moves Brilliant !! / Great ! / Best / Good / Inaccuracy ?! /
Mistake ? / Blunder ?? / Missed win, :class:`PlayClassifier` rates League moments from data the
engine already has: the official Live Client event feed + my stats (gold, health, ward score),
the gank threat of the minimap analysis, the enemy jungler's last sighting / fog circle, the
fight call of :mod:`treeaicoach.fight`, the minion waves of :mod:`treeaicoach.waves`, the Tab
scoreboard and the objective timers. Every :class:`Play` carries a one-line French reason.

Classes (:data:`CLASSES`, French titles in :data:`TITLE_FR`):

* ``brilliant`` "COUP DE MAÎTRE !!": outplay kill while behind (level / item gold) or outnumbered,
  steal of an epic monster, shutdown collected, multikill x3+, gank survived while outnumbered,
  objective taken after a perfect setup (wave pushed + new vision);
* ``great`` "EXCELLENT !": solo kill, double kill, gank survived, recall right after pushing the
  wave with gold to spend, objective taken with a numbers advantage;
* ``best`` "MEILLEUR COUP": the most fed enemy killed, objective secured with my participation;
* ``good`` "BON COUP": kill with help, clean recall;
* ``inaccuracy`` "IMPRÉCISION ?!": plain death, recall with no gold while my wave crashes into my tower;
* ``mistake`` "ERREUR ?": death after the RECULE call, death with 1300+ unspent gold;
* ``blunder`` "GAFFE ??": death right after a gank warning, shutdown given, death with 2000+
  gold, facecheck death (enemy jungler's fog circle on me, jungler in the kill);
* ``miss`` "OCCASION RATÉE": epic objective up with 2+ more players alive and nobody went,
  lane opponent dead + enemy jungler seen far away and the tower not taken.

Display policy (:meth:`PlayClassifier.update`): every rating is kept for the post-game summary,
but at most one is *shown* every :data:`MIN_GAP_S` (a ``brilliant`` skips the gap), nothing is
shown during a fight / gank threat except a *small* brilliant badge (the rest waits for the
end of the fight, :data:`TTL_S`), and the classes shown depend on the skill level
(:data:`SHOW_BY_SKILL`: an expert only sees brilliant / blunder / missed chances).

Post-game: :func:`summarize` -> counts per class + a 0-100 "précision" (:func:`precision`),
:func:`attach_to_record` writes it into the game record JSON (``record["plays"]``) and
:func:`summary_from_record` reads it back for the report / UI.

Riot policy: only official API data + screen pixels; no enemy cooldown / summoner spell timer.
Pure Python, thread-safe, never raises from the public API.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from treeaicoach.fmtutil import finite_loose

log = logging.getLogger(__name__)

CLASSES: tuple[str, ...] = ("brilliant", "great", "best", "good", "inaccuracy", "mistake", "blunder", "miss")
POSITIVE = frozenset({"brilliant", "great", "best", "good"})
NEGATIVE = frozenset({"inaccuracy", "mistake", "blunder", "miss"})
TITLE_FR: dict[str, str] = {
    "brilliant": "COUP DE MAÎTRE", "great": "EXCELLENT", "best": "MEILLEUR COUP", "good": "BON COUP",
    "inaccuracy": "IMPRÉCISION", "mistake": "ERREUR", "blunder": "GAFFE", "miss": "OCCASION RATÉE",
}
#: chess-style mark drawn in the badge icon ("" = a drawn glyph: star, check, cross)
SYMBOL: dict[str, str] = {
    "brilliant": "!!", "great": "!", "best": "", "good": "", "inaccuracy": "?!", "mistake": "?",
    "blunder": "??", "miss": "",
}
LABEL_FR: dict[str, str] = {   # plural labels of the post-game summary
    "brilliant": "Coups de maître", "great": "Excellents", "best": "Meilleurs coups", "good": "Bons coups",
    "inaccuracy": "Imprécisions", "mistake": "Erreurs", "blunder": "Gaffes", "miss": "Occasions ratées",
}
#: display priority (the most important pending rating is shown first)
PRIORITY: dict[str, int] = {"brilliant": 100, "blunder": 90, "great": 70, "miss": 65, "mistake": 60,
                            "best": 50, "inaccuracy": 30, "good": 20}
#: weight of each class in the 0-100 "précision" score
WEIGHT: dict[str, float] = {"brilliant": 100.0, "great": 100.0, "best": 100.0, "good": 90.0,
                            "inaccuracy": 60.0, "mistake": 30.0, "blunder": 0.0, "miss": 40.0}
PRIOR_N = 2            # neutral moments added to the score (a 2-moment game is not 0 or 100)
PRIOR_W = 75.0
SHOW_BY_SKILL: dict[str, frozenset[str]] = {
    "debutant": frozenset(CLASSES),
    "intermediaire": frozenset(CLASSES) - {"good"},
    "avance": frozenset({"brilliant", "great", "mistake", "blunder", "miss"}),
    "expert": frozenset({"brilliant", "blunder", "miss"}),
}

MIN_GAP_S = 45.0               # between two shown ratings (brilliant excepted)
BRILLIANT_GAP_S = 5.0          # even a brilliant waits for the previous badge to finish
TTL_S: dict[str, float] = {"brilliant": 40.0, "blunder": 45.0, "mistake": 45.0, "inaccuracy": 45.0}
DEFAULT_TTL_S = 30.0
QUIET_AFTER_FIGHT_S = 2.0      # big badges wait this long after a fight / gank threat
WARNING_DEATH_S = 12.0         # death this soon after a gank warning = ignored warning
REACTION_S = 3.0               # ...but only if the warning came at least this long before the death
DIVE_INVOLVED = 4              # this many enemies in my kill (a dive / a lost 5v5): never my blunder
TRADE_WINDOW_S = 10.0          # my team killed 2+ enemies this recently: a trade, not a thrown shutdown
FULL_BUILD_ITEMS = 6           # a full build keeps gold: dying with it is no mistake
RETREAT_DEATH_S = 8.0
FOG_DEATH_S = 6.0              # fog circle of the jungler on me this recently before the death
GOLD_MISTAKE = 1300
GOLD_BLUNDER = 2000
GANK_SURVIVE_S = 10.0
NEAR_R = 0.15                  # "near me" on the minimap (normalized, ~2200 game units)
FAR_R = 0.55                   # "far away" (other side of the map)
RECALL_GOLD = 1100
RECALL_LOW_GOLD = 450
WAVE_MEMORY_S = 12.0
SETUP_WARD_S = 120.0
SETUP_WAVE_S = 60.0
MISS_MIN_WINDOW_S = 20.0
MISS_ADVANTAGE = 2
MISS_RESPAWN_S = 20.0
TOWER_WINDOW_S = 35.0
TOWER_MIN_GT = 840.0           # laning over (2026: plates stay, but a tower is now a team objective)
EPIC_KEYS = ("dragon", "elder", "baron", "herald")
OBJ_EVENT: dict[str, tuple[str, str, str]] = {
    # EventName -> (key, "le X", "du X")
    "DragonKill": ("dragon", "le dragon", "du dragon"),
    "BaronKill": ("baron", "le Baron", "du Baron"),
    "HeraldKill": ("herald", "le Héraut", "du Héraut"),
    "HordeKill": ("grubs", "les larves", "des larves"),
}
OBJ_NAME: dict[str, str] = {"dragon": "Dragon", "elder": "Dragon ancestral", "baron": "Baron",
                            "herald": "Héraut", "grubs": "Larves"}
#: lane whose wave matters for an objective setup
OBJ_LANE: dict[str, str] = {"dragon": "bot", "elder": "bot", "baron": "top", "herald": "top",
                            "grubs": "top"}
ROLE_LANE: dict[str, str] = {"TOP": "top", "MIDDLE": "mid", "BOTTOM": "bot", "UTILITY": "bot"}


@dataclass(frozen=True)
class Play:
    """One rated moment."""

    cls: str                     # a key of CLASSES
    rule: str                    # rule id ("solo_kill", "death_after_warning"...)
    reason: str                  # one French line
    t: float                     # engine clock
    gt: float                    # game time (s)
    key: str                     # dedupe key
    alias: str | None = None     # champion concerned (victim / killer), for an icon
    size: str = "big"            # "big" | "small" (shown during a fight)

    @property
    def title(self) -> str:
        return TITLE_FR.get(self.cls, self.cls.upper())

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["title"] = self.title
        d["gt"] = round(float(self.gt), 1)
        d.pop("t", None)
        d.pop("size", None)
        return d


@dataclass
class PlayContext:
    """What the classifier sees at one tick (filled by :func:`build_context`, or by tests)."""

    t: float
    gt: float
    game: Any = None                                  # live_client.GameInfo
    threat: int = 0                                   # gank level this tick (0 / 1 warning / 2 danger)
    in_fight: bool = False
    fight_call: str | None = None                     # "engage" | "retreat" | None
    me_uv: tuple[float, float] | None = None
    in_base: bool = False
    allies_near: int = 0                              # visible allies near me (me excluded)
    enemies_near: int = 0                             # visible enemies near me
    jungler_uv: tuple[float, float] | None = None     # last known position of the enemy jungler
    jungler_seen_ago: float | None = None             # s since seen (0 = visible)
    jungler_fog_near: bool = False                    # his "possible zone" circle covers me
    jungler_far: bool = False                         # Tab intel: dead / just bought / farming the other side
    scoreboard: Any = None                            # scoreboard.ScoreboardSummary
    objectives: list[Any] = field(default_factory=list)   # objectives.ObjectiveState
    waves: dict[str, Any] = field(default_factory=dict)   # lane -> waves.LaneWave.as_dict()
    my_lane: str | None = None                        # "top" | "mid" | "bot" | None


def _f(x: Any, default: float = 0.0) -> float:
    return finite_loose(x, default)  # type: ignore[return-value]


def _truthy(x: Any) -> bool:
    if isinstance(x, str):
        return x.strip().lower() in ("true", "1", "yes")
    return bool(x)


def _names(p: Any) -> set[str]:
    out: set[str] = set()
    for attr in ("riot_id", "summoner_name"):
        v = str(getattr(p, attr, "") or "").strip()
        if v:
            out.add(v.casefold())
            out.add(v.split("#", 1)[0].casefold())
    return out


def _dist(a: Any, b: Any) -> float:
    try:
        return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))
    except Exception:
        return math.inf


def fmt_gold(n: Any) -> str:
    """``1 250 PO`` (narrow no-break space as the thousands separator)."""
    v = int(round(_f(n)))
    return f"{v:,}".replace(",", " ") + " PO"


def skill_of(cfg: Any) -> str:
    try:
        from treeaicoach.skill import normalize

        return normalize(getattr(cfg, "skill_level", "intermediaire"))
    except Exception:
        return "intermediaire"


# ======================================================================================
# Classifier
# ======================================================================================
class PlayClassifier:
    """See the module docstring. One instance per game (:meth:`reset` on a new game)."""

    def __init__(self, cfg: Any = None, *, min_gap_s: float = MIN_GAP_S) -> None:
        self._lock = threading.RLock()
        self.min_gap_s = float(min_gap_s)
        self.skill = "intermediaire"
        self.enabled = True
        self.apply_config(cfg)
        self.reset()

    # ------------------------------------------------------------------ config / state
    def apply_config(self, cfg: Any) -> None:
        try:
            if cfg is None:
                return
            with self._lock:
                self.skill = skill_of(cfg)
                self.enabled = bool(getattr(cfg, "plays_enabled", True))
        except Exception:
            log.exception("PlayClassifier.apply_config failed")

    def reset(self) -> None:
        with self._lock:
            self._seen: set[Any] = set()
            self._events_ready = False
            self._history: list[Play] = []
            self._pending: list[Play] = []
            self._shown: list[Play] = []
            self._last_shown_t = -math.inf
            self._last_quiet_t = -math.inf      # last tick with a fight / gank threat
            self._keys: set[str] = set()
            self._last_fetch: Any = None
            # per-tick memory
            self._was_dead = False
            self._last_dead_t = -math.inf
            self._gold_alive = 0.0
            self._hp_alive = 1.0
            self._warn_t = -math.inf
            self._warn_start = -math.inf        # start of the current gank warning episode
            self._retreat_t = -math.inf
            self._fog_near_t = -math.inf
            self._danger: dict[str, Any] | None = None   # current DANGER gank episode
            self._in_base = False
            self._out_of_base_t = -math.inf
            self._wave_push_t: dict[str, float] = {}     # lane -> last t its wave was "pushing"
            self._wave_in_t: dict[str, float] = {}       # lane -> last t pushed into my side
            self._wards: list[tuple[float, float]] = []  # (t, ward score)
            self._miss: dict[str, dict[str, Any]] = {}   # objective key -> open window
            self._tower: dict[str, Any] | None = None    # open "tower to take" window
            self._tower_done: set[Any] = set()
            self._struct_t = -math.inf                   # last enemy tower / inhibitor my team took
            self._deaths_near: list[tuple[float, int]] = []   # (t, enemies near me)

    def history(self) -> list[Play]:
        """Every rated moment of this game (shown or not), oldest first."""
        with self._lock:
            return list(self._history)

    def shown(self) -> list[Play]:
        with self._lock:
            return list(self._shown)

    def summary(self) -> dict[str, Any]:
        return summarize(self.history())

    def note(self, play: Play) -> None:
        """Add a rating produced elsewhere (tests, other analysers)."""
        with self._lock:
            self._add(play)

    # ------------------------------------------------------------------ tick
    def update(self, ctx: PlayContext) -> list[Play]:
        """One tick: rate what happened, return the rating(s) to show now (0 or 1). Never raises."""
        try:
            with self._lock:
                self._tick(ctx)
                return self._emit(ctx)
        except Exception:
            log.exception("PlayClassifier.update failed")
            return []

    def _tick(self, ctx: PlayContext) -> None:
        game = ctx.game
        me = getattr(game, "me", None) if game is not None else None
        if me is None:
            return
        t = float(ctx.t)
        dead = bool(getattr(me, "is_dead", False))
        if ctx.threat >= 1:
            if self._warn_t < t - 2.0 and not ctx.in_fight:     # a new warning episode (not a fight)
                self._warn_start = t
            self._warn_t = t
        if ctx.in_fight or ctx.threat >= 1:
            self._last_quiet_t = t
        if ctx.fight_call == "retreat":
            self._retreat_t = t
        if ctx.jungler_fog_near:
            self._fog_near_t = t
        if not dead:
            self._gold_alive = _f(getattr(game, "current_gold", 0.0))
            cs = getattr(game, "champion_stats", None) or {}
            mx = _f(cs.get("maxHealth"), 0.0)
            if mx > 0:
                self._hp_alive = max(0.0, min(1.0, _f(cs.get("currentHealth"), mx) / mx))
        else:
            self._last_dead_t = t
        for lane, w in (ctx.waves or {}).items():
            st = w.get("state") if isinstance(w, dict) else getattr(w, "state", None)
            if st == "pushing":
                self._wave_push_t[str(lane)] = t
            elif st == "pushed_in":
                self._wave_in_t[str(lane)] = t
        ws = _f(getattr(me, "ward_score", 0.0))
        if not self._wards or self._wards[-1][1] != ws:
            self._wards.append((t, ws))
            old = [x for x in self._wards if t - x[0] > 300.0]
            self._wards = old[-1:] + [x for x in self._wards if t - x[0] <= 300.0]   # + one baseline
        fetched = getattr(game, "fetched_at", None)
        if fetched is None or fetched != self._last_fetch:
            self._last_fetch = fetched
            self._events(ctx, me)
        self._gank_episode(ctx, dead)
        self._recall(ctx, dead)
        self._missed_objectives(ctx)
        self._missed_tower(ctx, me, dead)
        self._was_dead = dead

    # ------------------------------------------------------------------ helpers
    def _add(self, play: Play) -> None:
        if play.cls not in CLASSES or play.key in self._keys:
            return
        self._keys.add(play.key)
        self._history.append(play)
        self._pending.append(play)

    def _play(self, ctx: PlayContext, cls: str, rule: str, reason: str, key: str,
              alias: str | None = None) -> None:
        self._add(Play(cls, rule, reason, float(ctx.t), float(ctx.gt), key, alias))

    def _ward_gain(self, t: float, window: float) -> float:
        old = [w for tt, w in self._wards if t - tt >= window]
        base = old[-1] if old else (self._wards[0][1] if self._wards else 0.0)
        return (self._wards[-1][1] if self._wards else 0.0) - base

    @staticmethod
    def _alive_counts(game: Any) -> tuple[int, int, int]:
        """(allies alive incl. me, enemies alive, enemies dead for >= MISS_RESPAWN_S)."""
        allies = [game.me] + list(getattr(game, "allies", []) or [])
        enemies = list(getattr(game, "enemies", []) or [])
        a = sum(1 for p in allies if p is not None and not p.is_dead)
        e = sum(1 for p in enemies if not p.is_dead)
        long_dead = sum(1 for p in enemies if p.is_dead and _f(p.respawn_timer) >= MISS_RESPAWN_S)
        return a, e, long_dead

    def _lane_opponent(self, ctx: PlayContext, me: Any) -> Any:
        game = ctx.game
        m = getattr(ctx.scoreboard, "my_matchup", None)
        alias = getattr(m, "enemy_alias", None) if m is not None else None
        enemies = list(getattr(game, "enemies", []) or [])
        if alias:
            for p in enemies:
                if p.champion_alias == alias:
                    return p
        pos = str(getattr(me, "position", "") or "")
        if pos and pos != "JUNGLE":
            for p in enemies:
                if p.position == pos:
                    return p
        return None

    # ------------------------------------------------------------------ event feed
    def _events(self, ctx: PlayContext, me: Any) -> None:
        game = ctx.game
        events = [e for e in (getattr(game, "events", None) or []) if isinstance(e, dict)]
        new: list[dict] = []
        for e in events:
            k = e.get("EventID", (e.get("EventName"), e.get("EventTime")))
            if k in self._seen:
                continue
            self._seen.add(k)
            new.append(e)
        if not self._events_ready:          # joined mid-game: never rate the past
            self._events_ready = True
            return
        mine = _names(me)
        players: dict[str, Any] = {}
        for p in game.all_players():
            for n in _names(p):
                players.setdefault(n, p)
        my_team = getattr(me, "team", "")

        def who(name: Any) -> Any:
            n = str(name or "").strip().casefold()
            return players.get(n) or players.get(n.split("#", 1)[0])

        def is_me(name: Any) -> bool:
            n = str(name or "").strip().casefold()
            return bool(n) and (n in mine or n.split("#", 1)[0] in mine)

        multi = {int(_f(e.get("KillStreak"), 0)) for e in new
                 if e.get("EventName") == "Multikill" and is_me(e.get("KillerName"))}
        for e in new:
            name = e.get("EventName")
            assisters = [a for a in (e.get("Assisters") or []) if a] if isinstance(e.get("Assisters"), list) else []
            eid = e.get("EventID", f"{name}:{e.get('EventTime')}")
            if name == "ChampionKill" and is_me(e.get("KillerName")):
                self._my_kill(ctx, me, e, events, who(e.get("VictimName")), assisters, eid, bool(multi))
            elif name == "ChampionKill" and is_me(e.get("VictimName")):
                self._my_death(ctx, me, e, events, who(e.get("KillerName")), [who(a) for a in assisters], eid)
            elif name == "Multikill" and is_me(e.get("KillerName")):
                n = int(min(5, max(2, _f(e.get("KillStreak"), 2))))
                word = {2: "Double kill", 3: "Triple kill", 4: "Quadra kill", 5: "Pentakill"}[n]
                cls = "brilliant" if n >= 3 else "great"
                self._play(ctx, cls, "multikill", f"{word} : tu as gagné le combat.", f"multi:{eid}",
                           me.champion_alias)
            elif name in OBJ_EVENT:
                killer = who(e.get("KillerName"))
                team = getattr(killer, "team", None)
                if team is None:
                    teams = {getattr(who(a), "team", None) for a in assisters} - {None}
                    team = teams.pop() if len(teams) == 1 else None
                self._objective(ctx, me, e, name, team == my_team if team else None,
                                is_me(e.get("KillerName")) or any(is_me(a) for a in assisters), eid)
            elif name == "TurretKilled" and self._tower is not None:
                struct = str(e.get("TurretKilled") or "")
                owner = "ORDER" if "_T1_" in struct else "CHAOS" if "_T2_" in struct else None
                if owner is not None and owner != my_team:
                    self._tower["taken"] = True
            if name in ("TurretKilled", "InhibKilled"):
                struct = str(e.get("TurretKilled") or e.get("InhibKilled") or "")
                owner = "ORDER" if "_T1_" in struct else "CHAOS" if "_T2_" in struct else None
                if owner is not None and owner != my_team:
                    self._struct_t = float(ctx.t)

    @staticmethod
    def _streak(events: list[dict], kill: dict, names: set[str]) -> int:
        """Kills of a player (``names``) since their last death, before ``kill``."""
        t_kill = _f(kill.get("EventTime"))
        streak = 0
        for e in events:
            if e.get("EventName") != "ChampionKill" or e is kill or _f(e.get("EventTime")) > t_kill:
                continue
            if str(e.get("VictimName") or "").strip().casefold() in names:
                streak = 0
            elif str(e.get("KillerName") or "").strip().casefold() in names:
                streak += 1
        return streak

    def _my_kill(self, ctx: PlayContext, me: Any, e: dict, events: list[dict], victim: Any,
                 assisters: list[Any], eid: Any, multikill: bool) -> None:
        vname = getattr(victim, "champion_name", "") or "ta cible"
        valias = getattr(victim, "champion_alias", None)
        key = f"kill:{eid}"
        streak = self._streak(events, e, _names(victim)) if victim is not None else 0
        if streak >= 3:
            self._play(ctx, "brilliant", "shutdown", f"Shutdown sur {vname} : sa prime de {streak} kills "
                       "est pour toi.", key, valias)
            return
        if multikill:
            return                                  # the Multikill event rates the fight
        if not assisters:
            lvl_gap = int(getattr(victim, "level", 0) or 0) - int(getattr(me, "level", 0) or 0)
            gold_gap = self._item_gold_gap(ctx, me, victim)
            if lvl_gap >= 1:
                self._play(ctx, "brilliant", "outplay", f"Solo kill sur {vname} avec {lvl_gap} niveau"
                           f"{'x' if lvl_gap > 1 else ''} de retard.", key, valias)
            elif gold_gap >= 800:
                self._play(ctx, "brilliant", "outplay", f"Solo kill sur {vname} avec {fmt_gold(gold_gap)} "
                           "d'objets de retard.", key, valias)
            elif ctx.enemies_near >= 2:
                self._play(ctx, "brilliant", "outplay", f"{vname} éliminé seul contre {ctx.enemies_near}.",
                           key, valias)
            else:
                self._play(ctx, "great", "solo_kill", f"Solo kill sur {vname}.", key, valias)
            return
        fed = set(getattr(ctx.scoreboard, "fed", ()) or ())
        if valias and valias in fed:
            self._play(ctx, "best", "fed_kill", f"{vname}, l'ennemi le plus fort, est tombé.", key, valias)
        else:
            self._play(ctx, "good", "kill", f"{vname} éliminé avec ton équipe.", key, valias)

    @staticmethod
    def _item_gold_gap(ctx: PlayContext, me: Any, victim: Any) -> int:
        """Victim's item gold minus mine (Tab scoreboard), 0 when unknown."""
        try:
            lines = {pl.alias: pl for pl in getattr(ctx.scoreboard, "players", ()) or ()}
            a, b = lines.get(me.champion_alias), lines.get(getattr(victim, "champion_alias", None))
            if a is None or b is None:
                return 0
            return int(b.item_gold) - int(a.item_gold)
        except Exception:
            return 0

    def _my_death(self, ctx: PlayContext, me: Any, e: dict, events: list[dict], killer: Any,
                  helpers: list[Any], eid: Any) -> None:
        t = float(ctx.t)
        key = f"death:{eid}"
        kname = getattr(killer, "champion_name", "") or "l'ennemi"
        kalias = getattr(killer, "champion_alias", None)
        streak = self._streak(events, e, _names(me))
        gold = self._gold_alive
        jungler = ctx.game.enemy_jungler() if hasattr(ctx.game, "enemy_jungler") else None
        involved = [p for p in [killer] + helpers if p is not None]
        jungler_in = jungler is not None and any(p.champion_alias == jungler.champion_alias for p in involved)
        # V2 audit: never blame the player for what was out of his hands
        if len(involved) >= DIVE_INVOLVED:
            return                                   # 4-5 enemies on me: a dive / a lost teamfight
        T_death = _f(e.get("EventTime"))
        traded = self._team_kills_since(events, ctx.game, T_death - TRADE_WINDOW_S, T_death + 2.0)
        items = [i for i in (getattr(me, "items", None) or []) if isinstance(i, int) and i not in (3340, 3363, 3364)]
        full_build = len(items) >= FULL_BUILD_ITEMS
        warned = (self._warn_start > -math.inf and self._warn_start >= t - WARNING_DEATH_S
                  and t - self._warn_start >= REACTION_S)
        if streak >= 3 and traded >= 2:
            self._play(ctx, "inaccuracy", "shutdown_traded", f"Shutdown donné à {kname}, mais ton équipe a "
                       f"pris {traded} kills.", key, kalias)
        elif streak >= 3:
            self._play(ctx, "blunder", "shutdown_given", f"Shutdown donné à {kname} : ta prime de {streak} "
                       "kills est partie.", key, kalias)
        elif warned:
            ago = max(1, int(round(t - self._warn_start)))
            self._play(ctx, "blunder", "death_after_warning", f"Mort {ago} s après l'alerte de gank : "
                       "recule dès l'annonce.", key, kalias)
        elif jungler_in and t - self._fog_near_t <= FOG_DEATH_S:
            self._play(ctx, "blunder", "facecheck", f"Mort dans le brouillard : le cercle de "
                       f"{jungler.champion_name} était sur toi.", key, jungler.champion_alias)
        elif gold >= GOLD_BLUNDER and not full_build:
            self._play(ctx, "blunder", "death_gold", f"Mort avec {fmt_gold(gold)} non dépensés.", key, kalias)
        elif t - self._retreat_t <= RETREAT_DEATH_S:
            self._play(ctx, "mistake", "death_after_retreat", "Mort après l'appel RECULE.", key, kalias)
        elif gold >= GOLD_MISTAKE and not full_build:
            self._play(ctx, "mistake", "death_gold", f"Mort avec {fmt_gold(gold)} non dépensés.", key, kalias)
        else:
            n = len(involved)
            why = f"Mort face à {kname}" + (f" et {n - 1} autre{'s' if n > 2 else ''}" if n > 1 else "") + "."
            self._play(ctx, "inaccuracy", "death", why, key, kalias)

    @staticmethod
    def _team_kills_since(events: list[dict], game: Any, t0: float, t1: float) -> int:
        """Enemy champions killed (by my team) with an event time in [t0, t1]."""
        foes: set[str] = set()
        for p in getattr(game, "enemies", None) or []:
            foes |= _names(p)
        n = 0
        for ev in events:
            if ev.get("EventName") != "ChampionKill":
                continue
            T = _f(ev.get("EventTime"), -1.0)
            v = str(ev.get("VictimName") or "").strip().casefold()
            if t0 <= T <= t1 and (v in foes or v.split("#", 1)[0] in foes):
                n += 1
        return n

    def _objective(self, ctx: PlayContext, me: Any, e: dict, name: str, ours: bool | None,
                   participated: bool, eid: Any) -> None:
        okey, obj_le, _obj_du = OBJ_EVENT[name]
        if name == "DragonKill" and str(e.get("DragonType") or "").lower() == "elder":
            okey, obj_le = "elder", "le dragon ancestral"
        win = self._miss.get(okey)
        if win is not None:
            win["taken"] = True
        if ours is not True:
            return
        key = f"obj:{eid}"
        oname = OBJ_NAME.get(okey, "Objectif")
        if _truthy(e.get("Stolen")) and okey != "grubs":
            reason = f"Tu as volé {obj_le} !" if participated else f"Ton équipe a volé {obj_le} !"
            self._play(ctx, "brilliant", "steal", reason, key, me.champion_alias)
            return
        if not participated:
            return
        t = float(ctx.t)
        lane = OBJ_LANE.get(okey)
        wave_ok = any(t - self._wave_push_t.get(ln, -math.inf) <= SETUP_WAVE_S
                      for ln in {lane, ctx.my_lane} if ln)
        ward_ok = self._ward_gain(t, SETUP_WARD_S) >= 2.0
        a, en, _ = self._alive_counts(ctx.game)
        if wave_ok and ward_ok and okey in EPIC_KEYS:
            self._play(ctx, "brilliant", "setup", f"{oname} préparé parfaitement : vague poussée et "
                       "vision posée avant.", key, me.champion_alias)
        elif a - en >= 1:
            self._play(ctx, "great", "objective_numbers", f"{oname} pris à {a} contre {en}.", key,
                       me.champion_alias)
        else:
            self._play(ctx, "best", "objective", f"{oname} sécurisé avec ta participation.", key,
                       me.champion_alias)

    # ------------------------------------------------------------------ state rules
    def _gank_episode(self, ctx: PlayContext, dead: bool) -> None:
        t = float(ctx.t)
        ep = self._danger
        if ctx.threat >= 2 and not dead:
            if ep is None:
                ep = self._danger = {"start": t, "last": t, "peak": 0, "died": False}
            ep["last"] = t
            ep["peak"] = max(ep["peak"], int(ctx.enemies_near))
        if ep is None:
            return
        if dead:
            ep["died"] = True
        if t - ep["last"] >= GANK_SURVIVE_S:
            self._danger = None
            if not ep["died"] and not dead:
                key = f"gank:{int(ep['start'])}"
                if ep["peak"] >= 2 and ctx.allies_near == 0:
                    self._play(ctx, "brilliant", "gank_survived", f"Gank à {ep['peak']} contre 1 survécu "
                               "sans mourir.", key)
                else:
                    self._play(ctx, "great", "gank_survived", "Gank repéré et esquivé sans mourir.", key)

    def _recall(self, ctx: PlayContext, dead: bool) -> None:
        t = float(ctx.t)
        now_base = bool(ctx.in_base) and not dead
        if not ctx.in_base and not dead:
            self._out_of_base_t = t
        arrived = now_base and not self._in_base
        self._in_base = now_base
        if not arrived or t - self._last_dead_t < 90.0 or ctx.gt < 180.0:
            return                                    # respawn, or game start
        if t - self._out_of_base_t > 3.0:          # (a recall teleports: no walk through the base)
            return
        gold = _f(getattr(ctx.game, "current_gold", 0.0))
        lane = ctx.my_lane
        pushed = lane is not None and t - self._wave_push_t.get(lane, -math.inf) <= WAVE_MEMORY_S
        crashing_in = lane is not None and t - self._wave_in_t.get(lane, -math.inf) <= WAVE_MEMORY_S
        key = f"recall:{int(ctx.gt)}"
        if gold >= RECALL_GOLD and pushed:
            self._play(ctx, "great", "recall", f"Retour parfait : vague poussée et {fmt_gold(gold)} "
                       "à dépenser.", key)
        elif gold >= RECALL_GOLD:
            self._play(ctx, "good", "recall", f"Bon retour avec {fmt_gold(gold)} à dépenser.", key)
        elif gold < RECALL_LOW_GOLD and self._hp_alive >= 0.6 and crashing_in and ctx.gt >= 300.0:
            self._play(ctx, "inaccuracy", "recall_early", f"Retour avec {fmt_gold(gold)} et la vague "
                       "chez toi : des sbires perdus.", key)

    def _missed_objectives(self, ctx: PlayContext) -> None:
        t = float(ctx.t)
        game = ctx.game
        a, e, long_dead = self._alive_counts(game)
        adv = a - e >= MISS_ADVANTAGE and long_dead >= MISS_ADVANTAGE
        me_dead = bool(getattr(game.me, "is_dead", False))
        alive = {str(getattr(o, "key", "") or ""): o for o in ctx.objectives or ()
                 if getattr(o, "alive", False) and getattr(o, "key", "") in EPIC_KEYS}
        for okey in list(self._miss):
            win = self._miss[okey]
            if adv and okey in alive and not win.get("taken"):
                win["last"] = t
                win["best"] = max(win["best"], a - e)
                continue
            self._miss.pop(okey)
            traded = win["start"] <= self._struct_t <= win["last"] + 10.0     # we took a tower instead
            if not win.get("taken") and not traded and win["last"] - win["start"] >= MISS_MIN_WINDOW_S:
                oname = OBJ_NAME.get(okey, "Objectif")
                self._play(ctx, "miss", "missed_objective", f"{oname} disponible avec {win['best']} joueurs "
                           "de plus : personne n'y est allé.", f"miss:{okey}:{int(win['start'])}")
        if adv and not me_dead:
            for okey in alive:
                if okey not in self._miss:
                    self._miss[okey] = {"start": t, "last": t, "best": a - e, "taken": False}

    def _missed_tower(self, ctx: PlayContext, me: Any, dead: bool) -> None:
        t = float(ctx.t)
        win = self._tower
        if win is not None:
            if win.get("taken"):
                self._tower = None
            elif t - win["start"] >= TOWER_WINDOW_S:
                self._tower = None
                if not dead:
                    self._play(ctx, "miss", "missed_tower", f"{win['opp']} était mort et le jungler loin : "
                               "la tour était à prendre.", f"tower:{int(win['start'])}", win.get("alias"))
            return
        if dead or ctx.gt < TOWER_MIN_GT or ctx.me_uv is None or ctx.in_base or ctx.my_lane is None:
            return
        opp = self._lane_opponent(ctx, me)
        if opp is None or not opp.is_dead or _f(opp.respawn_timer) < 15.0:
            return
        sig = (opp.champion_alias, int(ctx.gt - _f(opp.respawn_timer)) // 20)
        if sig in self._tower_done:
            return
        far = ctx.jungler_far or (ctx.jungler_uv is not None and ctx.jungler_seen_ago is not None
                                  and ctx.jungler_seen_ago <= 8.0 and _dist(ctx.jungler_uv, ctx.me_uv) >= FAR_R)
        if not far:
            return
        try:
            from treeaicoach import geometry

            lane_now = geometry.lane_of(geometry.classify_zone(*ctx.me_uv))
        except Exception:
            lane_now = None
        if lane_now != ctx.my_lane:
            return
        if t - self._wave_push_t.get(ctx.my_lane, -math.inf) > WAVE_MEMORY_S:
            return                                    # no wave of mine at their tower: not a free tower
        self._tower_done.add(sig)
        self._tower = {"start": t, "opp": opp.champion_name or opp.champion_alias, "alias": opp.champion_alias,
                       "taken": False}

    # ------------------------------------------------------------------ output
    def _emit(self, ctx: PlayContext) -> list[Play]:
        t = float(ctx.t)
        self._pending = [p for p in self._pending if 0.0 <= t - p.t <= TTL_S.get(p.cls, DEFAULT_TTL_S)]
        if not self.enabled or not self._pending:
            return []
        show = SHOW_BY_SKILL.get(self.skill, SHOW_BY_SKILL["intermediaire"])
        self._pending = [p for p in self._pending if p.cls in show]
        if not self._pending:
            return []
        busy = ctx.in_fight or ctx.threat >= 1
        quiet = not busy and t - self._last_quiet_t >= QUIET_AFTER_FIGHT_S
        best = max(self._pending, key=lambda p: (PRIORITY.get(p.cls, 0), p.t))
        since = t - self._last_shown_t
        if best.cls == "brilliant":
            if since < BRILLIANT_GAP_S:
                return []
            size = "big" if quiet else "small"
        else:
            if not quiet or since < self.min_gap_s:
                return []
            size = "big"
        self._pending.remove(best)
        # one rating per moment: what happened at the same time is dropped from the display
        self._pending = [p for p in self._pending if abs(p.t - best.t) > 1.5]
        out = Play(best.cls, best.rule, best.reason, best.t, best.gt, best.key, best.alias, size)
        self._last_shown_t = t
        self._shown.append(out)
        return [out]


# ======================================================================================
# Engine glue
# ======================================================================================
def build_context(engine: Any, t: float, gt: float, game: Any, threat: int = 0,
                  me_uv: tuple[float, float] | None = None) -> PlayContext:
    """A :class:`PlayContext` from a running :class:`treeaicoach.engine.CoachEngine` (every piece
    ``getattr``-guarded: a missing analyser is simply left out). Never raises."""
    ctx = PlayContext(t=float(t), gt=float(gt), game=game, threat=int(threat or 0), me_uv=me_uv)
    try:
        from treeaicoach import geometry

        my_team = getattr(game, "my_team", None)
        if me_uv is not None:
            z = geometry.classify_zone(*me_uv)
            ctx.in_base = geometry.is_base(z) and geometry.zone_owner(z) == my_team
        tracker = getattr(engine, "_tracker", None)
        if tracker is not None and me_uv is not None:
            for tr in tracker.enemies(visible_only=True):
                p = tr.position()
                if p is not None and _dist(p, me_uv) <= NEAR_R:
                    ctx.enemies_near += 1
            allies = getattr(tracker, "allies", None)
            if callable(allies):
                for tr in allies(visible_only=True):
                    p = tr.position()
                    if p is not None and _dist(p, me_uv) <= NEAR_R:
                        ctx.allies_near += 1
        jungler = game.enemy_jungler() if game is not None and hasattr(game, "enemy_jungler") else None
        if jungler is not None and tracker is not None:
            tr = tracker.get(jungler.champion_alias)
            if tr is not None and tr.position() is not None:
                ctx.jungler_uv = tr.position()
                ctx.jungler_seen_ago = 0.0 if tr.visible else max(0.0, float(t) - float(tr.last_seen))
            fog = getattr(engine, "_fog", None)
            if fog is not None and me_uv is not None and not getattr(getattr(engine, "_cfg", None), "safe_mode", False):
                for est in fog.estimates():
                    if est.alias == jungler.champion_alias or getattr(est, "is_jungler", False):
                        if _dist(est.last_uv, me_uv) <= float(est.radius) + 0.03 and est.confidence > 0.2:
                            ctx.jungler_fog_near = True
        intel_t = getattr(engine, "_jungle_intel", None)
        intel = intel_t.state() if intel_t is not None and hasattr(intel_t, "state") else None
        tac = getattr(engine, "_tactics", None)
        if tac is not None:
            ctx.in_fight = bool(tac.in_fight())
            fs = tac.fight.state() if hasattr(tac, "fight") else None
            ctx.fight_call = getattr(fs, "call", None) if fs is not None and getattr(fs, "active", False) else None
        sb = getattr(engine, "scoreboard_summary", None)
        ctx.scoreboard = sb() if callable(sb) else None
        obj = getattr(engine, "_objectives", None)
        ctx.objectives = list(obj.states()) if obj is not None else []
        coach = getattr(engine, "_coach", None)
        if coach is not None and hasattr(coach, "waves"):
            ctx.waves = coach.waves() or {}
        roles = getattr(engine, "_role_resolver", None)
        role = roles.my_role() if roles is not None and hasattr(roles, "my_role") else None
        role = role or getattr(getattr(game, "me", None), "position", "") or None
        ctx.my_lane = ROLE_LANE.get(str(role or "").upper())
        if ctx.my_lane is None:
            lane_fn = getattr(engine, "my_observed_lane", None)
            lane = lane_fn() if callable(lane_fn) else None
            ctx.my_lane = lane if lane in ("top", "mid", "bot") else None
        if intel is not None and getattr(intel, "alias", None):     # jungle_intel.py (Tab data)
            side = getattr(intel, "farm_side", None)
            ctx.jungler_far = bool(getattr(intel, "dead", False) or getattr(intel, "recalled", False) or (
                getattr(intel, "farming", False) and side in ("top", "bot") and ctx.my_lane in ("top", "bot")
                and side != ctx.my_lane))
    except Exception:
        log.debug("Play context incomplete", exc_info=True)
    return ctx


# ======================================================================================
# Post-game summary ("précision")
# ======================================================================================
def precision(counts: dict[str, int]) -> int:
    """0-100 score (weighted mean of the ratings, :data:`WEIGHT`, with :data:`PRIOR_N` neutral moments)."""
    n = sum(int(counts.get(c, 0) or 0) for c in CLASSES)
    total = sum(WEIGHT[c] * int(counts.get(c, 0) or 0) for c in CLASSES)
    return int(round((total + PRIOR_N * PRIOR_W) / (n + PRIOR_N)))


def summarize(plays: Iterable[Any]) -> dict[str, Any]:
    """``{"counts": {cls: n}, "total": n, "precision": 0-100, "best": [...], "worst": [...], "plays": [...]}``.

    ``plays``: :class:`Play` objects or their ``to_dict()``. Never raises."""
    items: list[dict[str, Any]] = []
    for p in plays or ():
        try:
            d = p.to_dict() if isinstance(p, Play) else dict(p)
            if d.get("cls") in CLASSES:
                items.append(d)
        except Exception:
            continue
    counts = {c: sum(1 for d in items if d["cls"] == c) for c in CLASSES}
    pos = sorted((d for d in items if d["cls"] in POSITIVE), key=lambda d: -PRIORITY[d["cls"]])
    neg = sorted((d for d in items if d["cls"] in NEGATIVE), key=lambda d: -PRIORITY[d["cls"]])
    return {"schema": 1, "counts": counts, "total": len(items), "precision": precision(counts),
            "labels": dict(LABEL_FR), "best": pos[:3], "worst": neg[:3], "plays": items[:200]}


def summary_line(summary: dict[str, Any] | None) -> str:
    """``Précision 78 · 2 coups de maître · 1 gaffe`` (short French line for the UI / voice)."""
    if not summary:
        return ""
    c = summary.get("counts") or {}
    parts = [f"Précision {int(summary.get('precision', 0))}"]
    for cls, one, many in (("brilliant", "coup de maître", "coups de maître"), ("great", "excellent", "excellents"),
                           ("blunder", "gaffe", "gaffes"), ("miss", "occasion ratée", "occasions ratées")):
        n = int(c.get(cls, 0) or 0)
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    return " · ".join(parts)


def attach_to_record(record_path: Any, summary: dict[str, Any]) -> bool:
    """Write ``summary`` into the game record JSON as ``record["plays"]`` (atomic). Never raises."""
    try:
        p = Path(record_path)
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False
        data["plays"] = summary
        tmp = p.with_name(p.name + f".{os.getpid()}.plays.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, p)
        return True
    except Exception:
        log.exception("Cannot add the play summary to %s", record_path)
        return False


def summary_from_record(record: Any) -> dict[str, Any] | None:
    """The play summary of a game record (dict), recomputed from ``record["plays"]["plays"]``
    when present; None when the game has no rating (old record). Never raises."""
    try:
        block = record.get("plays") if isinstance(record, dict) else None
        if not isinstance(block, dict):
            return None
        plays = block.get("plays")
        return summarize(plays) if isinstance(plays, list) else block
    except Exception:
        return None


__all__ = ["CLASSES", "POSITIVE", "NEGATIVE", "TITLE_FR", "SYMBOL", "LABEL_FR", "SHOW_BY_SKILL", "Play",
           "PlayContext", "PlayClassifier", "build_context", "precision", "summarize", "summary_line",
           "attach_to_record", "summary_from_record"]
