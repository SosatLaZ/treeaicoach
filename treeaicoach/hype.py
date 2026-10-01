""""Mode annonceur": live win probability, esport caster lines and a shareable game summary.

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

log = logging.getLogger(__name__)

CASTER_STYLES = ("sobre", "coach", "caster")
DEFAULT_STYLE = "coach"
STYLE_LABELS = (("sobre", "Sobre"), ("coach", "Coach"), ("caster", "Caster esport"))

W_GOLD, W_KILL, W_TOWER = 0.30, 0.035, 0.12
W_DRAKE, W_SOUL, W_BARON, W_BUFF, W_ELDER, W_INHIB = 0.08, 0.55, 0.15, 0.65, 0.90, 0.35
P_MIN, P_MAX = 0.03, 0.97
BARON_BUFF_S, ELDER_BUFF_S, INHIB_RESPAWN_S = 180.0, 150.0, 300.0

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


def _gold_scale(gt: float) -> float:
    m = max(3.0, gt / 60.0)
    return min(1.6, max(0.55, math.sqrt(15.0 / m)))


def win_logit(f: WinFactors) -> float:
    g = (f.gold_diff or 0.0) / 1000.0
    return (W_GOLD * g * _gold_scale(f.game_time) + W_KILL * f.kill_diff + W_TOWER * f.tower_diff
            + W_DRAKE * f.dragon_diff + W_SOUL * f.soul + W_BARON * f.baron_diff + W_BUFF * f.baron_buff
            + W_ELDER * f.elder_buff + W_INHIB * f.inhib_diff)


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
    s = str(name or "")
    if "_T1_" in s or s.endswith("_T1"):
        return "ORDER"
    if "_T2_" in s or s.endswith("_T2"):
        return "CHAOS"
    return None


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
    gold = None
    if scoreboard is not None and getattr(scoreboard, "players", None):
        try:
            gold = float(scoreboard.team_gold_diff)
        except Exception:
            gold = None
    if gold is None:
        gold = 300.0 * (kills[mine] - kills[other]) + 500.0 * (towers[mine] - towers[other])
    soul = 1 if drakes[mine] >= 4 else -1 if drakes[other] >= 4 else 0
    return WinFactors(game_time=gt, gold_diff=gold, kill_diff=kills[mine] - kills[other],
                      tower_diff=towers[mine] - towers[other], dragon_diff=drakes[mine] - drakes[other],
                      soul=soul, baron_diff=barons[mine] - barons[other], baron_buff=baron_buff,
                      elder_buff=elder_buff, inhib_diff=inhib_down[other] - inhib_down[mine])


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
            return f"Retournement de situation ! Probabilité de victoire : {pct} pour cent !"
        return f"La partie bascule : {pct} pour cent de chances de victoire. Rien n'est joué, on reste groupés !"
    if up:
        return f"Probabilité de victoire : {pct} pour cent. L'avantage est pour nous, on joue les objectifs."
    return f"Probabilité de victoire : {pct} pour cent. On joue prudent et on attend une erreur adverse."


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
            self.history: list[tuple[float, float]] = []      # (game time, p)
            self._ref = 0.5
            self._last_swing = -math.inf
            self._last_hype = -math.inf
            self._seen: set[Any] = set()
            self._primed = False
            self._streaks: dict[str, int] = {}
            self._last_phrase: dict[str, str] = {}
            self.big_moments = 0

    # ------------------------------------------------------------------ queries
    def win_probability(self) -> float | None:
        return self._p

    def factors(self) -> WinFactors | None:
        return self._factors

    def hud_text(self) -> str | None:
        p = self._p
        return None if p is None else f"Victoire {fmt_pct(p)}"

    def stats(self) -> dict[str, Any]:
        """Per-game numbers for the shareable summary."""
        with self._lock:
            ps = [p for _t, p in self.history]
            if not ps:
                return {"big_moments": self.big_moments}
            lo, hi = min(ps), max(ps)
            i_lo = ps.index(lo)
            comeback = lo <= 0.35 and max(ps[i_lo:]) >= 0.6
            return {"win_prob_min": lo, "win_prob_max": hi, "win_prob_last": ps[-1], "comeback": comeback,
                    "big_moments": self.big_moments}

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
        if not self.history or gt - self.history[-1][0] >= 15.0:
            self.history.append((gt, p))
            del self.history[:-400]
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
                self.big_moments += 1
                out.append(line)
        return out


# ======================================================================================
# Shareable end-of-game summary
# ======================================================================================
ROLE_FR = {"TOP": "top", "JUNGLE": "jungle", "MIDDLE": "mid", "BOTTOM": "ADC", "UTILITY": "support"}


def _num(x: Any) -> float | None:
    try:
        f = float(x)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _dec(x: float, n: int = 1) -> str:
    return f"{x:.{n}f}".replace(".", ",")


def share_summary(analysis: Any, stats: dict[str, Any] | None = None) -> str:
    """A few lines to paste on Discord / social networks (French, no player name). Never raises."""
    try:
        a = analysis if isinstance(analysis, dict) else {}
        s = a.get("summary") if isinstance(a.get("summary"), dict) else {}
        res = {"Win": "Victoire", "Lose": "Défaite"}.get(s.get("result") or "", "Partie")
        dur = _num(s.get("duration")) or 0.0
        champ = s.get("champion_name") or s.get("champion") or "?"
        role = ROLE_FR.get(str(s.get("position") or "").upper())
        head = f"{res} en {int(round(dur / 60))} min avec {champ}" if dur >= 60 else f"{res} avec {champ}"
        lines = [f"TreeAI Coach — {head}{f' ({role})' if role else ''}"]
        parts = [f"KDA {int(s.get('kills') or 0)}/{int(s.get('deaths') or 0)}/{int(s.get('assists') or 0)}"]
        cspm = _num(s.get("cs_per_min"))
        if cspm is not None:
            parts.append(f"{_dec(cspm)} CS/min")
        vis = _num(s.get("vision_score"))
        if vis is not None:
            parts.append(f"vision {int(vis)}")
        kp = _num(s.get("kill_participation"))
        if kp is not None:
            parts.append(f"participation {int(round(kp * 100))} %")
        lines.append(" · ".join(parts))
        st = stats or {}
        lo, hi = _num(st.get("win_prob_min")), _num(st.get("win_prob_max"))
        if lo is not None and hi is not None:
            extra = " — belle remontée !" if st.get("comeback") else ""
            lines.append(f"Probabilité de victoire : de {fmt_pct(lo)} à {fmt_pct(hi)}{extra}")
        faced = int(a.get("ganks_faced") or 0)
        if faced:
            lines.append(f"Ganks : {int(a.get('ganks_survived') or 0)} survécus sur {faced}")
        tips = a.get("tip_items") or []
        warn = next((t for t in tips if isinstance(t, dict) and t.get("kind") == "warn"), None)
        if warn is not None and warn.get("text"):
            lines.append(f"À travailler : {str(warn['text']).strip()[:140]}")
        return "\n".join(lines)
    except Exception:
        log.exception("share_summary failed")
        return "TreeAI Coach — partie terminée."
