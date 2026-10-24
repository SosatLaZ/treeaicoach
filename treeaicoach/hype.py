""""Mode annonceur": live win probability and esport caster lines.

``cfg.caster_style``:

* ``"sobre"``  - nothing spoken by this module (the win probability is still shown);
* ``"coach"``  - (default) the win probability is spoken on big swings only;
* ``"caster"`` - plus short esport-style hype lines for the BIG moments only (multikill,
  shutdown, ace, Baron / Elder steal, solo kill): varied phrases, never an insult. The
  player's own big plays are already announced by :mod:`treeaicoach.praise`; in caster style
  :func:`restyle_praise` only adds a caster flourish to those sentences, and
  :class:`HypeCaster` covers the team events praise does not (an ally's triple kill, an ace...).

Win probability (:func:`win_probability`) - a hand-tuned logistic model, documented so that it
can be checked and re-fitted later::

    logit = W_GOLD  * (gold_diff / 1000) * s(t)        # Tab item-gold estimate (scoreboard.py)
          + W_KILL  * kill_diff
          + W_TOWER * tower_diff
          + W_DRAKE * dragon_diff     + W_SOUL  * soul       (+1 us / -1 them / 0)
          + W_BARON * baron_diff      + W_BUFF  * baron_buff (team with the active buff, 180 s)
          + W_ELDER * elder_buff      (150 s)
          + W_INHIB * inhibitors_down_diff                    (an inhibitor respawns after 300 s)
    s(t)  = clamp(sqrt(15 / max(minutes, 3)), 0.55, 1.6)    # 1 000 gold matter more early
    P     = clamp(1 / (1 + exp(-logit)), 3 %, 97 %)

With these weights a 3 000 gold lead is ~71 % at 15:00 and ~65 % at 30:00, an active Baron
buff is worth ~+15 points and the Elder buff ~+20 points at even gold, close to public
pro-play estimates. It only uses the official Live Client API (event feed, Tab items).

Calibration (2026-10, the user's 25 recorded rift games with a known result, 17 with the League
Client timeline; Brier score, 0.24 = always say the base rate; accuracy of p >= 50 %)::

    inputs                                   10 min          15 min          20 min
    exact (client timeline, n = 17/17/15)    0.152  88 %     0.128  82 %     0.153  80 %
    live, before (Tab gold only, no towers)  0.212  71 %     0.124  79 %     0.101  94 %
    live, now    (gold blend + towers)       0.170  83 %     0.111  83 %     0.114  89 %   (n = 24/24/18)

A single scale factor on the logit (0.7 .. 1.4) or other W_GOLD / W_KILL values moved the log loss
by < 0.02 (noise at this sample size): the weights are kept. What was wrong was the INPUT: the live
game names its structures "Turret_TChaos_L1_P3_..." (towers and inhibitors were never counted) and
the Tab item-gold diff misses everyone's unspent gold (see :func:`estimated_gold_diff`).

The probability is spoken (style != "sobre") when it moved by more than
:data:`SWING_POINTS` since the last spoken value, at most once every :data:`SWING_GAP_S`.

Pure Python, thread-safe, never raises from its public methods.
"""

from __future__ import annotations

import logging
import math
import random
import threading
from dataclasses import dataclass
from typing import Any

from treeaicoach.fmtutil import finite_loose as _num

log = logging.getLogger(__name__)

CASTER_STYLES = ("sobre", "coach", "caster")
DEFAULT_STYLE = "coach"
STYLE_LABELS = (("sobre", "Sobre"), ("coach", "Coach"), ("caster", "Caster esport"))

W_GOLD, W_KILL, W_TOWER = 0.30, 0.035, 0.12
W_DRAKE, W_SOUL, W_BARON, W_BUFF, W_ELDER, W_INHIB = 0.08, 0.55, 0.15, 0.65, 0.90, 0.35
P_MIN, P_MAX = 0.03, 0.97
BARON_BUFF_S, ELDER_BUFF_S, INHIB_RESPAWN_S = 180.0, 150.0, 300.0
W_LEVEL = 0.20               # per level of team-average lead (partly counted in the gold already)
#: team gold-lead estimate (see :func:`estimated_gold_diff`, least squares on real games, rounded)
GOLD_CS, GOLD_KILL, GOLD_TOWER, GOLD_TAB_W = 10.0, 340.0, 600.0, 0.3

SWING_POINTS = 15.0          # spoken when the probability moved by more than this (points)
SWING_GAP_S = 180.0          # ... at most once every 3 minutes
SWING_MIN_GT = 300.0         # not before 5:00
HYPE_GAP_S = 15.0            # min. interval between two caster lines
EVENT_MAX_AGE_S = 20.0       # older events (app started mid-game) are never announced
SHUTDOWN_STREAK = 3


# ======================================================================================
# Win probability
# ======================================================================================
@dataclass(frozen=True)
class WinFactors:
    """Everything the model uses, from MY team's point of view (positive = good for us)."""

    game_time: float = 0.0
    gold_diff: float | None = None
    kill_diff: int = 0
    tower_diff: int = 0
    dragon_diff: int = 0
    soul: int = 0
    baron_diff: int = 0
    baron_buff: int = 0
    elder_buff: int = 0
    inhib_diff: int = 0
    level_diff: float = 0.0      # my team's average level - theirs


def _gold_scale(gt: float) -> float:
    m = max(3.0, gt / 60.0)
    return min(1.6, max(0.55, math.sqrt(15.0 / m)))


def win_logit(f: WinFactors) -> float:
    g = (f.gold_diff or 0.0) / 1000.0
    return (W_GOLD * g * _gold_scale(f.game_time) + W_KILL * f.kill_diff + W_TOWER * f.tower_diff
            + W_DRAKE * f.dragon_diff + W_SOUL * f.soul + W_BARON * f.baron_diff + W_BUFF * f.baron_buff
            + W_ELDER * f.elder_buff + W_INHIB * f.inhib_diff + W_LEVEL * (f.level_diff or 0.0))


def win_probability(f: WinFactors) -> float:
    """Probability (0..1) that MY team wins. Never raises (0.5 on garbage)."""
    try:
        x = win_logit(f)
        if not math.isfinite(x):
            return 0.5
        p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))
        return min(P_MAX, max(P_MIN, p))
    except Exception:
        return 0.5


def _names(p: Any) -> set[str]:
    out = set()
    for attr in ("riot_id", "summoner_name"):
        v = str(getattr(p, attr, "") or "").strip()
        if v:
            out.add(v.casefold())
            out.add(v.split("#", 1)[0].casefold())
    return out


def team_lookup(game: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for p in game.all_players():
            for n in _names(p):
                out.setdefault(n, p.team)
    except Exception:
        pass
    return out


def _structure_owner(name: Any) -> str | None:
    """Owner team of a structure: the live game sends "Turret_TChaos_L1_P3_..." (not only the old
    documented "Turret_T2_..."), phase.structure_owner reads both."""
    from treeaicoach.phase import structure_owner

    return structure_owner(name)


def _killer_team(ev: dict, lookup: dict[str, str]) -> str | None:
    t = lookup.get(str(ev.get("KillerName") or "").casefold())
    if t:
        return t
    teams = {lookup.get(str(a).casefold()) for a in (ev.get("Assisters") or [])[:10]} - {None}
    return teams.pop() if len(teams) == 1 else None


def _ftime(ev: dict) -> float:
    try:
        v = float(ev.get("EventTime") or 0.0)
        return v if math.isfinite(v) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _team_cs_diff(game: Any) -> float | None:
    """My team's creep score - theirs (Live Client ``scores.creepScore``, steps of 10 in game)."""
    try:
        ours = [p for p in [game.me, *(getattr(game, "allies", None) or [])] if p is not None]
        theirs = list(getattr(game, "enemies", None) or [])
        if not ours or not theirs:
            return None
        cs = lambda ps: sum(_num((getattr(p, "scores", None) or {}).get("creepScore")) or 0.0 for p in ps)  # noqa: E731
        return cs(ours) - cs(theirs)
    except Exception:
        return None


def estimated_gold_diff(game: Any, kill_diff: int, tower_diff: int, scoreboard: Any = None) -> float:
    """Team gold lead estimate from what the Live Client exposes (gold of the other players is
    hidden). Calibrated on 197 frames of 17 real games against the League Client timeline (exact
    total gold): the Tab item-gold diff alone (``scoreboard.team_gold_diff``: unspent gold
    invisible, a fresh purchase swings it by 3 000) is off by 2 650 RMS; the earned-gold estimate
    ``GOLD_CS * cs + GOLD_KILL * kills + GOLD_TOWER * towers`` by 1 700, and the blend
    ``GOLD_TAB_W * Tab + (1 - GOLD_TAB_W) * earned`` by ~1 600 (correlation 0.95)."""
    earned = GOLD_KILL * kill_diff + GOLD_TOWER * tower_diff + GOLD_CS * (_team_cs_diff(game) or 0.0)
    tab = None
    if scoreboard is not None and getattr(scoreboard, "players", None):
        try:
            tab = float(scoreboard.team_gold_diff)
        except Exception:
            tab = None
    if tab is None or not math.isfinite(tab):
        return earned
    return GOLD_TAB_W * tab + (1.0 - GOLD_TAB_W) * earned


def factors_from_game(game: Any, scoreboard: Any = None) -> WinFactors | None:
    """:class:`WinFactors` from a Live Client snapshot (+ Tab summary). None when spectating."""
    me = getattr(game, "me", None)
    if me is None:
        return None
    mine = me.team
    gt = float(getattr(game, "game_time", 0.0) or 0.0)
    lookup = team_lookup(game)
    kills = {"ORDER": 0, "CHAOS": 0}
    towers = {"ORDER": 0, "CHAOS": 0}
    drakes = {"ORDER": 0, "CHAOS": 0}
    barons = {"ORDER": 0, "CHAOS": 0}
    inhib_down: dict[str, int] = {"ORDER": 0, "CHAOS": 0}     # inhibitors of this team currently down
    baron_buff = elder_buff = 0
    for ev in getattr(game, "events", None) or []:
        if not isinstance(ev, dict):
            continue
        name = ev.get("EventName")
        et = _ftime(ev)
        if name == "ChampionKill":
            t = lookup.get(str(ev.get("KillerName") or "").casefold())
            victim_t = lookup.get(str(ev.get("VictimName") or "").casefold())
            if t and victim_t and t != victim_t:
                kills[t] += 1
            elif victim_t:                       # executed / killed by a minion: credit the other team
                kills["CHAOS" if victim_t == "ORDER" else "ORDER"] += 1
        elif name == "TurretKilled":
            owner = _structure_owner(ev.get("TurretKilled"))
            if owner:
                towers["CHAOS" if owner == "ORDER" else "ORDER"] += 1
        elif name == "InhibKilled":
            owner = _structure_owner(ev.get("InhibKilled"))
            if owner and gt - et < INHIB_RESPAWN_S:
                inhib_down[owner] += 1
        elif name == "DragonKill":
            t = _killer_team(ev, lookup)
            if t is None:
                continue
            if str(ev.get("DragonType") or "").casefold() == "elder":
                if gt - et < ELDER_BUFF_S:
                    elder_buff = 1 if t == mine else -1
            else:
                drakes[t] += 1
        elif name == "BaronKill":
            t = _killer_team(ev, lookup)
            if t is None:
                continue
            barons[t] += 1
            if gt - et < BARON_BUFF_S:
                baron_buff = 1 if t == mine else -1
    other = "CHAOS" if mine == "ORDER" else "ORDER"
    gold = estimated_gold_diff(game, kills[mine] - kills[other], towers[mine] - towers[other], scoreboard)
    soul = 1 if drakes[mine] >= 4 else -1 if drakes[other] >= 4 else 0
    level_diff = 0.0
    try:
        ours = [int(p.level) for p in [me, *(getattr(game, "allies", None) or [])]]
        theirs = [int(p.level) for p in (getattr(game, "enemies", None) or [])]
        if ours and theirs:
            level_diff = round(sum(ours) / len(ours) - sum(theirs) / len(theirs), 2)
    except Exception:
        level_diff = 0.0
    return WinFactors(game_time=gt, gold_diff=gold, kill_diff=kills[mine] - kills[other],
                      tower_diff=towers[mine] - towers[other], dragon_diff=drakes[mine] - drakes[other],
                      soul=soul, baron_diff=barons[mine] - barons[other], baron_buff=baron_buff,
                      elder_buff=elder_buff, inhib_diff=inhib_down[other] - inhib_down[mine],
                      level_diff=level_diff if math.isfinite(level_diff) else 0.0)


def fmt_pct(p: float | None) -> str:
    return "—" if p is None else f"{int(round(100.0 * p))} %"


# ======================================================================================
# Caster lines
# ======================================================================================
PHRASES: dict[str, tuple[str, ...]] = {
    "ally_multi": ("{word} pour {name} ! Quelle démonstration !", "{name} enchaîne, {word} ! Incroyable !",
                   "Et c'est un {word} signé {name} ! Le public est debout !"),
    "ace_us": ("ACE ! Toute l'équipe adverse est au sol, on fonce sur les objectifs !",
               "Et c'est l'ACE ! Plus personne en face, c'est le moment de tout prendre !",
               "ACE ! Quel combat d'équipe ! Baron, tours, tout est ouvert !"),
    "ace_them": ("Ace adverse. On respire, on défend ensemble et on attend la prochaine.",
                 "Combat perdu, on se regroupe calmement et on protège la base."),
    "steal_us": ("VOLÉ ! {name} arrache {obj_le} sous leur nez !", "Quel vol de {name} ! {obj_le} est à nous !",
                 "Incroyable ! {name} vole {obj_le} !"),
    "steal_them": ("{obj_le} nous échappe sur un vol. On se reconcentre, la partie continue.",
                   "Vol adverse sur {obj_le}, dommage. On reste groupés."),
    "shutdown_ally": ("SHUTDOWN ! {name} met fin à la série de {victim} !",
                      "{name} fait taire {victim} ! Énorme shutdown !"),
}
PRAISE_FLOURISH: dict[str, tuple[str, ...]] = {
    "multi:": ("Oh là là, quel enchaînement !", "Mais quelle action !", "Le stade explose !"),
    "shutdown:": ("Énorme shutdown !", "Et la prime tombe !", "Quel shutdown !"),
    "solo:": ("Un contre un, et c'est propre !", "Duel remporté !", "Quelle mécanique !"),
    "steal:": ("C'EST VOLÉ !", "Incroyable, quel vol !", "Quel culot !"),
}
MULTI_WORD = {3: "Triplé", 4: "Quadra kill", 5: "PENTAKILL"}
OBJ_LE = {"baron": "le Baron", "elder": "le dragon ancestral", "dragon": "le dragon", "herald": "le Héraut"}


def normalize_style(value: Any) -> str:
    v = str(value or "").strip().lower()
    return v if v in CASTER_STYLES else DEFAULT_STYLE


def restyle_praise(key: str, text: str, style: Any, rng: random.Random | None = None) -> str:
    """In caster style, add a hype flourish in front of a big-moment praise sentence."""
    try:
        if normalize_style(style) != "caster":
            return text
        for prefix, opts in PRAISE_FLOURISH.items():
            if str(key).startswith(prefix):
                return f"{(rng or random).choice(opts)} {text}"
    except Exception:
        pass
    return text


def swing_phrase(p: float, prev: float, style: str) -> str:
    pct = int(round(100 * p))
    up = p > prev
    if style == "caster":
        if up:
            return f"Retournement de situation ! Victoire : {pct} % !"
        return f"La partie bascule : victoire {pct} %. Rien n'est joué, restez groupés !"
    # the advice follows the LEVEL, not the direction of the swing ("50 % : l'avantage est pour nous" was wrong)
    if pct >= 55:
        return f"Victoire {pct} % : on a l'avantage, jouez les objectifs."
    if pct <= 45:
        return f"Victoire {pct} % : jouez prudent, attendez leur erreur."
    return f"Victoire {pct} % : partie serrée, groupez-vous aux objectifs."


# ======================================================================================
# Live announcer
# ======================================================================================
class HypeCaster:
    """Win-probability tracker + caster lines. ``update`` returns the sentences to SPEAK."""

    def __init__(self, cfg: Any = None, seed: int | None = None) -> None:
        self._lock = threading.Lock()
        self._rng = random.Random(seed)
        self._style = DEFAULT_STYLE
        self.apply_config(cfg)
        self.reset()

    def apply_config(self, cfg: Any) -> None:
        self._style = normalize_style(getattr(cfg, "caster_style", DEFAULT_STYLE))

    @property
    def style(self) -> str:
        return self._style

    def reset(self) -> None:
        with self._lock:
            self._p: float | None = None
            self._factors: WinFactors | None = None
            self._ref = 0.5
            self._last_swing = -math.inf
            self._last_hype = -math.inf
            self._seen: set[Any] = set()
            self._primed = False
            self._streaks: dict[str, int] = {}
            self._last_phrase: dict[str, str] = {}

    # ------------------------------------------------------------------ queries
    def win_probability(self) -> float | None:
        return self._p

    def factors(self) -> WinFactors | None:
        return self._factors

    # ------------------------------------------------------------------ live
    def update(self, t: float, game: Any, scoreboard: Any = None, threat: int = 0) -> list[str]:
        try:
            with self._lock:
                return self._update(float(t), game, scoreboard, int(threat or 0))
        except Exception:
            log.exception("HypeCaster.update failed")
            return []

    def _update(self, t: float, game: Any, scoreboard: Any, threat: int) -> list[str]:
        f = factors_from_game(game, scoreboard)
        if f is None:
            return []
        self._factors = f
        p = win_probability(f)
        self._p = p
        gt = f.game_time
        out: list[str] = []
        lines = self._events(game, gt)
        if self._style == "caster" and lines and threat < 1 and t - self._last_hype >= HYPE_GAP_S:
            out.append(lines[0])
            self._last_hype = t
        if (self._style != "sobre" and threat < 1 and gt >= SWING_MIN_GT
                and abs(p - self._ref) * 100.0 > SWING_POINTS and t - self._last_swing >= SWING_GAP_S
                and not out):
            out.append(swing_phrase(p, self._ref, self._style))
            self._ref = p
            self._last_swing = t
        return out

    def _pick(self, kind: str, **kw: Any) -> str:
        opts = [o for o in PHRASES[kind] if o != self._last_phrase.get(kind)] or list(PHRASES[kind])
        s = self._rng.choice(opts)
        self._last_phrase[kind] = s
        return s.format(**kw)

    def _events(self, game: Any, gt: float) -> list[str]:
        """Caster lines of NEW big team events not already praised (the player's own plays)."""
        me = game.me
        mine = me.team
        my_names = _names(me)
        lookup = team_lookup(game)
        champ: dict[str, str] = {}
        for p in game.all_players():
            for n in _names(p):
                champ[n] = p.champion_name or p.champion_alias
        out: list[str] = []
        first = not self._primed
        self._primed = True
        for i, ev in enumerate(getattr(game, "events", None) or []):
            if not isinstance(ev, dict):
                continue
            eid = ev.get("EventID")
            uid = ("id", eid) if isinstance(eid, int) else ("i", i, ev.get("EventName"), _ftime(ev))
            if uid in self._seen:
                continue
            self._seen.add(uid)
            name = ev.get("EventName")
            killer = str(ev.get("KillerName") or "").casefold()
            if name == "ChampionKill":           # kill streaks (shutdowns), even for old events
                victim = str(ev.get("VictimName") or "").casefold()
                streak = self._streaks.get(victim, 0)
                self._streaks[victim] = 0
                if killer in lookup:
                    self._streaks[killer] = self._streaks.get(killer, 0) + 1
            else:
                streak = 0
            if first or gt - _ftime(ev) > EVENT_MAX_AGE_S:
                continue
            line = None
            involved = killer in my_names or any(str(a).casefold() in my_names for a in ev.get("Assisters") or [])
            if name == "Multikill":
                n = int(ev.get("KillStreak") or 0)
                if n >= 3 and lookup.get(killer) == mine and killer not in my_names:
                    line = self._pick("ally_multi", word=MULTI_WORD.get(min(n, 5), "Triplé"),
                                      name=champ.get(killer, "notre équipe"))
            elif name == "Ace":
                team = str(ev.get("AcingTeam") or "")
                if team in ("ORDER", "CHAOS"):
                    line = self._pick("ace_us" if team == mine else "ace_them")
            elif name in ("BaronKill", "DragonKill") and str(ev.get("Stolen")).casefold() == "true":
                if name == "DragonKill" and str(ev.get("DragonType") or "").casefold() != "elder":
                    obj = "dragon"
                else:
                    obj = "baron" if name == "BaronKill" else "elder"
                team = _killer_team(ev, lookup)
                if team == mine and not involved:
                    line = self._pick("steal_us", name=champ.get(killer, "notre équipe"), obj_le=OBJ_LE[obj])
                elif team is not None and team != mine and obj in ("baron", "elder"):
                    line = self._pick("steal_them", obj_le=OBJ_LE[obj][:1].upper() + OBJ_LE[obj][1:])
            elif name == "ChampionKill" and streak >= SHUTDOWN_STREAK:
                if lookup.get(killer) == mine and killer not in my_names:
                    victim = str(ev.get("VictimName") or "").casefold()
                    line = self._pick("shutdown_ally", name=champ.get(killer, "notre équipe"),
                                      victim=champ.get(victim, "l'adversaire"))
            if line:
                out.append(line)
        return out
