"""Tab scoreboard analysis from the official Live Client Data API (public information only).

Everything used here is what any player sees on the Tab scoreboard: champion, level, K/D/A,
creep score, ward score and items of the 10 players (``allPlayers``), plus the public event
feed (kills, objectives). No enemy cooldown, no hidden information.

:class:`ScoreboardAnalyzer` is fed once per Live Client poll and produces

* an immutable :class:`ScoreboardSummary`: per player :class:`PlayerLine` (KDA, CS, CS/min,
  level, item gold value from the bundled Data Dragon table ``assets/items.json``), the lane
  :class:`MatchupDiff` by role (TOP vs TOP...), the team gold difference, who is fed / who is
  struggling, the recent power spikes and a compact text summary for the HUD / UI /
  post-game report (:meth:`ScoreboardSummary.to_dict`);
* rare one-shot :class:`ScoreInsight` (French voice sentence + toast texts):

  - ``fed``      an enemy with a big lead: "Attention, Darius est très avancé : 4/0, +1500 PO."
  - ``spike``    my lane opponent (or a fed enemy / the enemy jungler) completed a major item,
                 or my lane opponent reached level 6 / 11 / 16 before me;
  - ``struggle`` an ally who keeps dying (many deaths, deaths in a short time, or repeatedly
                 seen alone deep in the enemy half right before dying - tracker positions).

Gold is estimated from the *items* (unspent gold of other players is not public): this is the
"value on the Tab screen", the same for everybody, so diffs are fair.

Pure Python, thread-safe, never raises from its public methods.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

log = logging.getLogger(__name__)

ROLE_ORDER: tuple[str, ...] = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
ROLE_SHORT: dict[str, str] = {"TOP": "TOP", "JUNGLE": "JGL", "MIDDLE": "MID", "BOTTOM": "ADC",
                              "UTILITY": "SUP"}

# -------------------------------------------------------------------------- tunables
FED_MIN_KILLS = 4               # an enemy is "fed" with >= 4 kills and kills - deaths >= 3 ...
FED_MIN_KD_DIFF = 3
FED_GOLD_LEAD = 1500            # ... or >= 1500 item gold ahead of his lane opponent / the average
FED_TIER2_KD = 7                # second announcement ("de plus en plus avancé")
FED_TIER2_GOLD = 3000
FED_MIN_GT = 240.0
STRUGGLE_MIN_DEATHS = 4         # ally: >= 4 deaths and deaths >= kills + assists / 2 + 3
STRUGGLE_KDA_MARGIN = 3
STRUGGLE_FAST_DEATHS = 3        # ... or 3 deaths within 5 game minutes
STRUGGLE_FAST_WINDOW = 300.0
DEEP_ALONE_RADIUS = 0.2         # no other ally within this (normalized minimap) -> "alone"
DEEP_DEATH_WINDOW = 20.0        # seen alone deep at most this long (engine s) before a death
DEEP_DEATHS = 2                 # deaths after being alone deep -> "goes in alone"
SPIKE_LEVELS = (6, 11, 16)
INSIGHT_GAP_S = 25.0            # min engine time between two insights from this module
INSIGHT_TTL_S = 40.0            # a queued insight is dropped after this
SUMMARY_MAX_SPIKES = 8

_ITEMS: dict[int, tuple[str, int, str]] | None = None
_items_lock = threading.Lock()


# -------------------------------------------------------------------------- item table
def item_table() -> dict[int, tuple[str, int, str]]:
    """``itemID -> (name, total gold, kind)`` from ``assets/items.json`` (cached; {} if absent)."""
    global _ITEMS
    with _items_lock:
        if _ITEMS is not None:
            return _ITEMS
        table: dict[int, tuple[str, int, str]] = {}
        try:
            from treeaicoach.paths import asset_path

            data = json.loads(asset_path("items.json").read_text(encoding="utf-8"))
            for k, v in (data.get("items") or {}).items():
                try:
                    table[int(k)] = (str(v.get("n") or ""), int(v.get("g") or 0), str(v.get("k") or "other"))
                except (TypeError, ValueError, AttributeError):
                    continue
        except Exception:
            log.warning("Item price table unavailable (assets/items.json)", exc_info=True)
        _ITEMS = table
        return table


def item_info(item_id: Any) -> tuple[str, int, str] | None:
    try:
        return item_table().get(int(item_id))
    except (TypeError, ValueError):
        return None


def items_gold(items: Iterable[Any]) -> int:
    """Total Data Dragon value of an inventory (unknown items count 0)."""
    total = 0
    for it in items or ():
        info = item_info(it)
        if info is not None:
            total += info[1]
    return total


def major_items(items: Iterable[Any]) -> list[int]:
    """Completed major (legendary) items of an inventory, inventory order."""
    out = []
    for it in items or ():
        info = item_info(it)
        if info is not None and info[2] == "legendary":
            out.append(int(it))
    return out


# -------------------------------------------------------------------------- helpers
def _finite(x: Any, default: float = 0.0) -> float:
    try:
        f = float(x)
    except (TypeError, ValueError, OverflowError):
        return default
    return f if math.isfinite(f) else default


def fmt_gold(n: float, signed: bool = True) -> str:
    """French gold amount: "+1 500 PO" / "-300 PO" (narrow no-break space)."""
    v = int(round(n / 50.0) * 50) if abs(n) >= 1000 else int(round(n / 10.0) * 10)
    s = f"{abs(v):,}".replace(",", " ")
    sign = ("+" if v > 0 else "-" if v < 0 else "") if signed else ("-" if v < 0 else "")
    return f"{sign}{s} PO"


def fmt_signed(n: float) -> str:
    v = int(round(n))
    return f"+{v}" if v > 0 else str(v)


def fmt_dec(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def player_names(p: Any) -> set[str]:
    """Names under which the event feed may refer to a player."""
    out: set[str] = set()
    for attr in ("riot_id", "summoner_name"):
        v = str(getattr(p, attr, "") or "").strip()
        if v:
            out.add(v)
            out.add(v.split("#", 1)[0])
    return {n.casefold() for n in out if n}


# -------------------------------------------------------------------------- data
@dataclass(frozen=True)
class PlayerLine:
    alias: str
    name: str                    # localized champion name
    team: str
    side: str                    # "self" | "ally" | "enemy"
    role: str | None
    kills: int
    deaths: int
    assists: int
    cs: int
    cs_per_min: float
    level: int
    item_gold: int               # Data Dragon value of the items (Tab)
    est_gold: int                # item gold (+ my unspent gold for me)
    major_items: int
    ward_score: float
    is_dead: bool = False

    @property
    def kda(self) -> str:
        return f"{self.kills}/{self.deaths}/{self.assists}"


@dataclass(frozen=True)
class MatchupDiff:
    role: str
    ally: str                    # champion names
    enemy: str
    ally_alias: str
    enemy_alias: str
    gold_diff: int               # ally - enemy (item gold)
    cs_diff: int
    level_diff: int
    kills_diff: int
    involves_me: bool = False

    @property
    def text(self) -> str:
        return (f"{ROLE_SHORT.get(self.role, self.role)} {self.ally} vs {self.enemy} : "
                f"{fmt_gold(self.gold_diff)}, {fmt_signed(self.cs_diff)} CS, {fmt_signed(self.level_diff)} niv")


@dataclass(frozen=True)
class ScoreInsight:
    kind: str                    # "fed" | "spike" | "struggle"
    key: str
    text: str                    # French voice sentence
    title: str                   # toast title (short, upper case)
    subtitle: str                # toast subtitle
    toast_kind: str              # "warning" | "insight" | "danger"
    alias: str | None = None
    t: float = 0.0


@dataclass(frozen=True)
class ScoreboardSummary:
    game_time: float = 0.0
    players: tuple[PlayerLine, ...] = ()
    matchups: tuple[MatchupDiff, ...] = ()
    ally_gold: int = 0
    enemy_gold: int = 0
    ally_kills: int = 0
    enemy_kills: int = 0
    fed: tuple[str, ...] = ()            # aliases of fed enemies
    struggling: tuple[str, ...] = ()     # aliases of struggling allies
    spikes: tuple[str, ...] = ()         # recent spike texts ("Darius : Estropieur")
    my_matchup: MatchupDiff | None = None

    @property
    def team_gold_diff(self) -> int:
        return self.ally_gold - self.enemy_gold

    def lines(self) -> list[str]:
        """Compact Tab summary (one line per lane matchup + the team line)."""
        out = [m.text for m in self.matchups]
        out.append(f"Équipe {fmt_gold(self.team_gold_diff)} · kills {self.ally_kills}-{self.enemy_kills}")
        return out

    def hud_line(self) -> str | None:
        """One HUD line: my lane diff + team gold diff."""
        if not self.players:
            return None
        team = f"équipe {fmt_gold(self.team_gold_diff)}"
        m = self.my_matchup
        if m is not None:
            return f"Ta voie : {fmt_signed(m.cs_diff)} sbires, {fmt_gold(m.gold_diff)} · {team}"
        return f"Tab : {team}, kills {self.ally_kills}-{self.enemy_kills}"

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly representation (recorder / post-game report)."""
        return {
            "game_time": round(self.game_time, 1),
            "ally_gold": self.ally_gold, "enemy_gold": self.enemy_gold,
            "team_gold_diff": self.team_gold_diff,
            "ally_kills": self.ally_kills, "enemy_kills": self.enemy_kills,
            "players": [asdict(p) for p in self.players],
            "matchups": [dict(asdict(m), text=m.text) for m in self.matchups],
            "fed": list(self.fed), "struggling": list(self.struggling), "spikes": list(self.spikes),
            "lines": self.lines(),
        }


@dataclass
class _PState:
    majors: list[int] = field(default_factory=list)
    level: int = 1
    deaths: int = 0
    death_times: list[float] = field(default_factory=list)   # game times of deaths (counted)
    deep_seen: float | None = None                           # engine t when last seen alone deep
    deep_deaths: int = 0
    fed_tier: int = 0
    struggle_done: bool = False


# -------------------------------------------------------------------------- analyzer
class ScoreboardAnalyzer:
    """See the module docstring. ``update`` once per Live Client poll (cheap)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._summary = ScoreboardSummary()
            self._st: dict[tuple[str, str], _PState] = {}
            self._baseline = False
            self._queue: list[ScoreInsight] = []
            self._last_emit = -math.inf
            self._spikes: list[str] = []
            self._last_fetch: Any = None

    # ------------------------------------------------------------------ public
    def summary(self) -> ScoreboardSummary:
        with self._lock:
            return self._summary

    def update(self, game: Any, t: float, roles: Any = None, tracker: Any = None,
               threat: int = 0) -> list[ScoreInsight]:
        """Analyse one poll; returns at most one new insight. Never raises."""
        try:
            with self._lock:
                return self._update_locked(game, float(t), roles, tracker, int(threat or 0))
        except Exception:
            log.exception("ScoreboardAnalyzer.update failed")
            return []

    def track_positions(self, t: float, tracker: Any, game: Any) -> None:
        """Per tick: remember allies seen alone deep in the enemy half (for "struggle"). Never raises."""
        try:
            with self._lock:
                self._track_deep(float(t), tracker, game)
        except Exception:
            log.debug("track_positions failed", exc_info=True)

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _role(p: Any, side: str, roles: Any) -> str | None:
        r = None
        if roles is not None:
            try:
                r = roles.role_of(p.champion_alias, "enemy" if side == "enemy" else "ally")
            except Exception:
                r = None
        return r or (p.position or None)

    def _line(self, p: Any, side: str, role: str | None, gt: float) -> PlayerLine:
        ig = items_gold(p.items)
        est = ig + (int(_finite(getattr(p, "current_gold", 0.0))) if side == "self" else 0)
        cs = int(p.creep_score)
        return PlayerLine(
            alias=p.champion_alias, name=p.champion_name or p.champion_alias, team=p.team, side=side,
            role=role, kills=p.kills, deaths=p.deaths, assists=p.assists, cs=cs,
            cs_per_min=round(cs / (gt / 60.0), 1) if gt >= 60.0 else 0.0, level=int(p.level),
            item_gold=ig, est_gold=est, major_items=len(major_items(p.items)),
            ward_score=round(float(p.ward_score), 1), is_dead=bool(p.is_dead))

    def _update_locked(self, game: Any, t: float, roles: Any, tracker: Any, threat: int) -> list[ScoreInsight]:
        if game is None or getattr(game, "me", None) is None:
            return []
        gt = max(0.0, _finite(getattr(game, "game_time", 0.0)))
        fetched = getattr(game, "fetched_at", None)
        if fetched is None or fetched != self._last_fetch:
            self._last_fetch = fetched
            self._analyse(game, gt, t, roles)
        return self._emit(t, threat)

    def _analyse(self, game: Any, gt: float, t: float, roles: Any) -> None:
        me = game.me
        lines: list[PlayerLine] = []
        by_key: dict[tuple[str, str], PlayerLine] = {}
        players: list[tuple[Any, str]] = [(me, "self")] + [(p, "ally") for p in game.allies] + \
            [(p, "enemy") for p in game.enemies]
        for p, side in players:
            ln = self._line(p, side, self._role(p, side, roles), gt)
            lines.append(ln)
            by_key[(ln.alias, "enemy" if side == "enemy" else "ally")] = ln
        allies = [ln for ln in lines if ln.side != "enemy"]
        enemies = [ln for ln in lines if ln.side == "enemy"]
        # lane matchups by role
        matchups: list[MatchupDiff] = []
        my_m = None
        for role in ROLE_ORDER:
            a = next((ln for ln in allies if ln.role == role and ln.side == "self"), None) or \
                next((ln for ln in allies if ln.role == role), None)
            e = next((ln for ln in enemies if ln.role == role), None)
            if a is None or e is None:
                continue
            m = MatchupDiff(role=role, ally=a.name, enemy=e.name, ally_alias=a.alias, enemy_alias=e.alias,
                            gold_diff=a.item_gold - e.item_gold, cs_diff=a.cs - e.cs,
                            level_diff=a.level - e.level, kills_diff=a.kills - e.kills,
                            involves_me=a.side == "self")
            matchups.append(m)
            if m.involves_me:
                my_m = m
        opp_of = {m.enemy_alias: m for m in matchups}
        all_avg = sum(ln.item_gold for ln in lines) / max(1, len(lines))

        # deaths from the scores + game times of deaths from the event feed
        death_gt = self._death_times(game)
        first = not self._baseline
        fed: list[str] = []
        struggling: list[str] = []
        for p, side in players:
            key = (p.champion_alias, "enemy" if side == "enemy" else "ally")
            ln = by_key[key]
            st = self._st.setdefault(key, _PState(majors=major_items(p.items), level=ln.level, deaths=ln.deaths))
            names = player_names(p)
            st.death_times = sorted(g for n, g in death_gt if n in names)
            if ln.deaths > st.deaths and st.deep_seen is not None and t - st.deep_seen <= DEEP_DEATH_WINDOW:
                st.deep_deaths += 1
                st.deep_seen = None
            if side == "enemy":
                m = opp_of.get(ln.alias)
                lead = -m.gold_diff if m is not None else ln.item_gold - all_avg
                if self._is_fed(ln, lead, gt):
                    fed.append(ln.alias)
                    tier = 2 if (ln.kills - ln.deaths >= FED_TIER2_KD or lead >= FED_TIER2_GOLD) else 1
                    if tier > st.fed_tier:
                        st.fed_tier = tier
                        self._queue_fed(ln, lead, tier, t)
                self._spike_check(ln, st, p, my_m, roles, game, first, t)
            elif side == "ally":
                if self._is_struggling(ln, st, gt):
                    struggling.append(ln.alias)
                    if not st.struggle_done and gt >= FED_MIN_GT:
                        st.struggle_done = True
                        self._queue_struggle(ln, st, gt, t)
            st.majors = major_items(p.items)
            st.level = ln.level
            st.deaths = ln.deaths
        self._baseline = True
        self._summary = ScoreboardSummary(
            game_time=gt, players=tuple(lines), matchups=tuple(matchups),
            ally_gold=sum(ln.item_gold for ln in allies), enemy_gold=sum(ln.item_gold for ln in enemies),
            ally_kills=sum(ln.kills for ln in allies), enemy_kills=sum(ln.kills for ln in enemies),
            fed=tuple(fed), struggling=tuple(struggling), spikes=tuple(self._spikes[-SUMMARY_MAX_SPIKES:]),
            my_matchup=my_m)

    @staticmethod
    def _death_times(game: Any) -> list[tuple[str, float]]:
        out = []
        for ev in getattr(game, "events", None) or []:
            if isinstance(ev, dict) and ev.get("EventName") == "ChampionKill":
                v = str(ev.get("VictimName") or "").strip().casefold()
                if v:
                    out.append((v, _finite(ev.get("EventTime"))))
                    out.append((v.split("#", 1)[0], _finite(ev.get("EventTime"))))
        return out

    @staticmethod
    def _is_fed(ln: PlayerLine, lead: float, gt: float) -> bool:
        if gt < FED_MIN_GT:
            return False
        kd = ln.kills >= FED_MIN_KILLS and ln.kills - ln.deaths >= FED_MIN_KD_DIFF
        gold = lead >= FED_GOLD_LEAD and ln.kills - ln.deaths >= 1
        return kd or gold

    @staticmethod
    def _is_struggling(ln: PlayerLine, st: _PState, gt: float) -> bool:
        many = ln.deaths >= STRUGGLE_MIN_DEATHS and ln.deaths >= ln.kills + ln.assists / 2 + STRUGGLE_KDA_MARGIN
        dt = st.death_times
        fast = False
        if len(dt) >= STRUGGLE_FAST_DEATHS:
            recent = dt[-STRUGGLE_FAST_DEATHS:]
            fast = recent[-1] - recent[0] <= STRUGGLE_FAST_WINDOW and recent[-1] >= gt - 60.0
        return many or fast or st.deep_deaths >= DEEP_DEATHS

    def _push(self, ins: ScoreInsight) -> None:
        self._queue = [q for q in self._queue if q.key != ins.key] + [ins]

    def _queue_fed(self, ln: PlayerLine, lead: float, tier: int, t: float) -> None:
        kda = f"{ln.kills}/{ln.deaths}"
        gold = f", {fmt_gold(lead)}" if lead >= 500 else ""
        if tier >= 2:
            text = f"{ln.name} est de plus en plus avancé : {kda}{gold}. Évite-le seul."
        else:
            text = f"Attention, {ln.name} est très avancé : {kda}{gold}."
        self._push(ScoreInsight("fed", f"fed:{ln.alias}:{tier}", text, "ENNEMI AVANCÉ",
                                 f"{ln.name} {ln.kda}{gold}", "warning", ln.alias, t))

    def _queue_struggle(self, ln: PlayerLine, st: _PState, gt: float, t: float) -> None:
        if st.deep_deaths >= DEEP_DEATHS:
            why = "meurt seul en territoire ennemi"
        elif ln.deaths >= STRUGGLE_MIN_DEATHS:
            why = f"est à {ln.kda}"
        else:
            why = "meurt souvent"
        text = f"{ln.name} {why} : ne joue pas autour de lui, protège-toi."
        self._push(ScoreInsight("struggle", f"struggle:{ln.alias}", text, "ALLIÉ EN DIFFICULTÉ",
                                 f"{ln.name} {ln.kda} — en difficulté", "insight", ln.alias, t))

    def _spike_check(self, ln: PlayerLine, st: _PState, p: Any, my_m: MatchupDiff | None, roles: Any,
                     game: Any, first: bool, t: float) -> None:
        majors = major_items(p.items)
        new = [i for i in majors if i not in st.majors]
        is_opp = my_m is not None and my_m.enemy_alias == ln.alias
        try:
            jg = game.enemy_jungler()
            is_jg = jg is not None and jg.champion_alias == ln.alias
        except Exception:
            is_jg = False
        if new and not first:
            info = item_info(new[-1])
            iname = info[0] if info else "un objet"
            self._spikes.append(f"{ln.name} : {iname}")
            if is_opp or is_jg or st.fed_tier > 0:
                n = len(majors)
                nth = "premier" if n == 1 else f"{n}e"
                self._push(ScoreInsight(
                    "spike", f"spike:{ln.alias}:{new[-1]}",
                    f"{ln.name} a terminé {iname} : il devient plus fort.", "ENNEMI PLUS FORT",
                    f"{ln.name} — {iname} ({nth} objet)", "warning", ln.alias, t))
        if is_opp and not first and ln.level > st.level:
            me = game.me
            for lv in SPIKE_LEVELS:
                if st.level < lv <= ln.level:
                    self._spikes.append(f"{ln.name} : niveau {lv}")
                    ahead = int(getattr(me, "level", 0) or 0) < lv
                    if ahead:
                        self._push(ScoreInsight(
                            "spike", f"level:{ln.alias}:{lv}",
                            f"{ln.name} est niveau {lv} avant toi : prudence.", f"NIVEAU {lv}",
                            f"{ln.name} passe niveau {lv}", "warning", ln.alias, t))

    def _track_deep(self, t: float, tracker: Any, game: Any) -> None:
        if tracker is None or game is None or getattr(game, "me", None) is None:
            return
        from treeaicoach import geometry

        my_team = game.my_team
        allies = [tr for tr in tracker.allies(visible_only=True)] + (
            [tracker.me()] if tracker.me() is not None else [])
        pos = {}
        for tr in allies:
            p = tr.position() if tr is not None else None
            if p is not None:
                pos[tr.key] = (tr, p)
        for key, (tr, p) in pos.items():
            if getattr(tr, "relation", "") == "self" or not tr.alias:
                continue
            z = geometry.classify_zone(*p)
            # enemy half: the side of the river opposite to my team's base
            enemy_half = (p[1] < p[0]) if my_team == "ORDER" else (p[1] > p[0])
            deep = enemy_half and (geometry.is_jungle(z) or geometry.is_base(z))
            if not deep:
                continue
            alone = all(geometry.dist(p, q) > DEEP_ALONE_RADIUS for k2, (_t2, q) in pos.items() if k2 != key)
            if alone:
                st = self._st.get((tr.alias, "ally"))
                if st is not None:
                    st.deep_seen = t

    def _emit(self, t: float, threat: int) -> list[ScoreInsight]:
        self._queue = [q for q in self._queue if t - q.t <= INSIGHT_TTL_S]
        if not self._queue or threat > 0 or t - self._last_emit < INSIGHT_GAP_S:
            return []
        order = {"fed": 0, "spike": 1, "struggle": 2}
        self._queue.sort(key=lambda q: (order.get(q.kind, 9), -q.t))
        ins = self._queue.pop(0)
        self._last_emit = t
        return [ins]
