"""Written coaching advice (HUD, never spoken): a data-driven library + a utility ranker.

:data:`TIPS` holds concrete League of Legends advice in plain French, each written as
"WHAT to do : WHY" in at most :data:`MAX_WORDS` words with live numbers and names
("Joue prudemment sous ta tour : Darius a 2 niveaux d'avance", "Rentre acheter une balise de contrôle :
dragon dans 70 s"). Each :class:`Tip` has a condition on a :class:`TipContext` (role, lane
matchup, levels / gold / CS gaps, objective timers, wave, enemy jungler, enemies around me,
deaths, gold, base...), a priority (urgency), a tone (HUD accent colour), the confidence of its
data and a validity window. Vague always-true tips ("regarde ta minimap") are not in the
library; "play safe" only appears with its concrete reason.

:class:`TipRotator` shows the ONE most useful applicable tip (utility = urgency x relevance x
confidence, generic tips dropped whenever a specific one applies), holds it a few seconds,
replaces it at once when it stops applying (never stale) or when something much more useful
appears, and rotates after its validity window (each tip then on cooldown). :func:`build_context` makes the context from the coach facts
(:meth:`treeaicoach.coach.MapCoach.facts`), the Live Client data and the Tab summary.

Only the minimap facts the coach already uses + the official Live Client API (my own data and
the public Tab scoreboard). Pure Python, thread-safe, never raises from its public API.
"""

from __future__ import annotations

import logging
import random
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from treeaicoach.fmtutil import finite as _f

log = logging.getLogger(__name__)

ROTATE_S = 25.0                 # a tip is shown at most this long when others apply
DEFAULT_COOLDOWN_S = 480.0      # the same tip is not shown again for 8 min (game time)

ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
CS_TARGET = {"TOP": 7.0, "MIDDLE": 7.0, "BOTTOM": 7.5, "JUNGLE": 5.5, "UTILITY": 0.0}
SIDE_FR = {"top": "en haut", "mid": "au milieu", "bot": "en bas"}
LANE_FR = {"top": "top", "mid": "mid", "bot": "bot"}
CONTROL_WARD = 2055
SWEEPER = 3364
BOOTS = frozenset({1001, 3006, 3009, 3020, 3047, 3111, 3117, 3158, 3010, 2422})


def _k(n: float) -> str:
    """1234 -> "1,2k", 800 -> "800" (gold amounts, French decimal comma)."""
    n = abs(int(n))
    return f"{n / 1000:.1f}k".replace(".", ",") if n >= 1000 else str(n)


@dataclass
class TipContext:
    """Everything a tip condition may look at (defaults = unknown / neutral)."""

    gt: float = 0.0
    role: str | None = None              # TOP / JUNGLE / MIDDLE / BOTTOM / UTILITY
    lane: str | None = None              # top / mid / bot (of my role)
    my_lane: str | None = None           # lane I am standing in now (minimap), None in the jungle / base
    dead: bool = False
    in_base: bool = False
    level: int = 1
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    cs: int = 0
    cspm: float = 0.0
    cs_target: float = 7.0
    ward_score: float = 0.0
    gold: float = 0.0
    items: frozenset = frozenset()
    hp: float | None = None              # 0..1 (activePlayer.championStats)
    opp: str | None = None               # lane opponent name
    opp_level: int | None = None
    opp_dead: bool = False
    dead_names: tuple[str, ...] = ()     # enemies dead right now (names)
    level_diff: int = 0                  # me - lane opponent
    gold_diff: int = 0                   # Tab item gold
    cs_diff: int = 0
    team_gold_diff: int = 0
    fed: tuple[str, ...] = ()            # names of fed enemies
    soon: dict = field(default_factory=dict)     # objective key -> seconds before spawn (<= 120)
    alive: frozenset = frozenset()               # objective keys up now
    obj_names: dict = field(default_factory=dict)  # objective key -> display name
    wave: str | None = None              # "pushing" | "pushed_in" | "even" | None
    jg: str | None = None                # enemy jungler name
    jg_visible: bool = False
    jg_side: str | None = None           # top / mid / bot (visible)
    jg_last_side: str | None = None
    jg_hidden_s: float | None = None
    jg_dead: bool = False
    missing: int = 0
    enemies_near: int = 0                # enemies visible around me (coach facts "numbers")
    allies_near: int = 1                 # allies around me, me included
    stance: str | None = None
    recent_deaths: int = 0               # my deaths in the last 5 min
    safe: bool = False
    item: str | None = None              # next recommended item (itemization)
    buy_names: str | None = None         # components affordable now ("Phage + Épée longue")
    buy_value: int = 0                   # their price
    # power spikes vs my lane opponent (spikes.SpikeTracker): who reached a key level / item first
    spike_who: str | None = None         # "me" | "opp"
    spike_what: str | None = None        # "level" | "item"
    spike_level: int = 0
    spike_item: str = ""
    # power plays (phase.MapState via game_plan.map_fields)
    soul: str | None = None              # "us" | "them": team on soul point
    baron_buff_s: float = 0.0
    enemy_baron_s: float = 0.0
    elder_buff_s: float = 0.0
    enemy_elder_s: float = 0.0
    # session goal (goals.GoalTracker) + game-start plan (game_plan.matchup_card)
    goal_kind: str | None = None
    goal_target: float = 0.0
    goal_risk: str | None = None         # "last_death" | "cs_behind"
    plan1: str | None = None
    plan2: str | None = None
    plan_jg: str | None = None
    has_tp: bool = True                  # my summoner spells include Teleport (True when unknown)
    # cross-system consistency (engine): tone of the active macro call, recall already said
    macro_tone: str | None = None        # "go" | "danger" | None
    recall_said: bool = False            # a recall reminder / "rentre" call was shown recently
    dead_respawn: float = 0.0            # shortest respawn timer of the dead enemies (s), 0 = none

    # ---- helpers used by the conditions
    @property
    def minute(self) -> float:
        return self.gt / 60.0

    @property
    def early(self) -> bool:
        return self.gt < 840.0

    @property
    def mid(self) -> bool:
        return 840.0 <= self.gt < 1500.0

    @property
    def late(self) -> bool:
        return self.gt >= 1500.0

    @property
    def laner(self) -> bool:
        return self.role in ("TOP", "MIDDLE", "BOTTOM", "UTILITY")

    @property
    def jg_on_me(self) -> bool:
        """Their jungler was seen on my side of the map recently (not dead)."""
        return (not self.jg_dead and self.jg_last_side is not None and self.jg_last_side == self.lane
                and self.jg_hidden_s is not None and self.jg_hidden_s < 25)

    def jg_objective(self) -> str | None:
        """'le dragon' / 'le Héraut'... free because their jungler is dead: up (or <= 20 s), for my
        role, never Baron / Elder with only the jungler down."""
        from treeaicoach.game_plan import OBJ_ROLES
        for key in ("dragon", "herald", "grubs", "baron", "elder"):
            up = key in self.alive or (self.soon.get(key) is not None and self.soon[key] <= 20)
            if not up or (self.role is not None and self.role not in OBJ_ROLES.get(key, ROLES)):
                continue
            if key in ("baron", "elder") and len(self.dead_names) < 2:
                continue
            return OBJ_LE.get(key, "l'objectif")
        return None

    @property
    def has_control_ward(self) -> bool:
        return CONTROL_WARD in self.items

    @property
    def has_boots(self) -> bool:
        return bool(BOOTS & self.items)

    @property
    def side_lane(self) -> bool:
        """Standing in a side lane (top / bot) right now."""
        return self.my_lane in ("top", "bot")

    def soon_within(self, key: str, lo: float, hi: float) -> bool:
        r = self.soon.get(key)
        return r is not None and lo <= r <= hi

    def next_objective(self, hi: float = 120.0) -> tuple[str, float] | None:
        """(key, seconds) of the soonest objective spawning within ``hi`` s."""
        best = min(((r, k) for k, r in self.soon.items() if 0 <= r <= hi), default=None)
        return (best[1], best[0]) if best is not None else None

    def my_objective(self, lo: float, hi: float) -> tuple[str, float] | None:
        """(key, seconds) of the soonest objective spawning in [lo, hi] s that concerns my role."""
        from treeaicoach.game_plan import OBJ_ROLES
        best = min(((r, k) for k, r in self.soon.items() if lo <= r <= hi
                    and (self.role is None or self.role in OBJ_ROLES.get(k, ROLES))), default=None)
        return (best[1], best[0]) if best is not None else None

    def obj(self, key: str, default: str) -> str:
        return str(self.obj_names.get(key) or default)

    def fmt(self) -> dict[str, Any]:
        def secs(key: str) -> int:
            r = self.soon.get(key)
            return int(round((r or 0) / 5.0) * 5)
        nxt = self.next_objective()
        lvl = abs(self.level_diff)
        opp_side = {"top": "en bas", "bot": "en haut"}.get(self.jg_side or "", "de l'autre côté")
        return {
            "opp": self.opp or "ton vis-à-vis", "jg": self.jg or "leur jungler",
            "cspm": f"{self.cspm:.1f}".replace(".", ","), "target": f"{self.cs_target:g}".replace(".", ","),
            "lane": LANE_FR.get(self.lane or "", "ta voie"), "jg_side": SIDE_FR.get(self.jg_side or "", "loin"),
            "jg_last": SIDE_FR.get(self.jg_last_side or "", "loin"),
            "other": SIDE_FR.get({"top": "bot", "bot": "top"}.get(self.jg_last_side or "", ""), "ailleurs"),
            "jg_opp_side": opp_side,
            "drag_s": secs("dragon"), "baron_s": secs("baron"), "herald_s": secs("herald"),
            "grubs_s": secs("grubs"), "elder_s": secs("elder"),
            "fed": self.fed[0] if self.fed else "leur carry", "lvl": lvl,
            "lvl_txt": f"{lvl} niveau{'x' if lvl > 1 else ''}",
            "gold": int(self.gold), "deaths": self.deaths, "missing": self.missing,
            "ward": int(self.ward_score), "gd": _k(self.gold_diff), "tgd": _k(self.team_gold_diff),
            "cs_gap": abs(self.cs_diff), "rd": self.recent_deaths,
            "hp_pct": int(round(100 * (self.hp if self.hp is not None else 1.0))),
            "en": self.enemies_near, "al": self.allies_near,
            "next_obj": self.obj(nxt[0], nxt[0]) if nxt else "prochain objectif",
            "next_s": int(round(nxt[1] / 5.0) * 5) if nxt else 60,
            "jg_h": int(self.jg_hidden_s or 0), "my_lane": SIDE_FR.get(self.my_lane or "", "de côté"),
            "dead": self.dead_names[0] if self.dead_names else (self.opp or "un ennemi"),
            "item": self.item or "ton prochain objet",
            "buy": self.buy_names or "ton composant",
            "sp_lvl": self.spike_level, "sp_item": self.spike_item or "un gros objet",
            "baron_left": int(self.baron_buff_s // 5 * 5), "ebaron": int(self.enemy_baron_s // 5 * 5),
            "elder_left": int(self.elder_buff_s // 5 * 5), "eelder": int(self.enemy_elder_s // 5 * 5),
            "goal_t": f"{self.goal_target:g}".replace(".", ","),
            "my_obj": self.obj(mo[0], mo[0]) if (mo := self.my_objective(0, 130)) else "prochain objectif",
            "my_le": OBJ_LE.get(mo[0], "l'objectif") if mo else "l'objectif",
            "next_le": OBJ_LE.get(nxt[0], "l'objectif") if nxt else "l'objectif",
            "my_obj_s": int(round(mo[1] / 5.0) * 5) if mo else 60,
            "plan1": self.plan1 or "Tue vite la première vague : le premier niveau 2 gagne",
            "plan2": self.plan2 or "Reste derrière tes sbires : ils prennent les coups à ta place",
            "plan_jg": self.plan_jg or "Balise ta rivière vers 2:30 : premier gank possible",
            "jgobj": self.jg_objective() or "l'objectif",
            "n_dead": len(self.dead_names), "resp": int(self.dead_respawn),
        }


#: objective key -> "le dragon" (French article included)
OBJ_LE = {"dragon": "le dragon", "baron": "le Baron", "herald": "le Héraut", "grubs": "les larves",
          "elder": "l'ancestral"}

#: Tone of a tip (HUD accent colour): "danger" (red), "warning" (amber), "go" (green), "info" (gold)
TONES = ("danger", "warning", "go", "info")
#: utility weight of each priority tier (urgency)
PRIO_URGENCY = {4: 1.0, 3: 0.75, 2: 0.45, 1: 0.2}
MAX_WORDS = 12


@dataclass(frozen=True)
class Tip:
    id: str
    category: str
    text: str                                  # "Action : raison" -> str.format(**ctx.fmt())
    when: Callable[[TipContext], bool] = lambda c: True
    roles: tuple[str, ...] | None = None       # None = every role
    prio: int = 1                              # 1 generic, 2 role / phase, 3 contextual, 4 urgent
    cooldown: float = DEFAULT_COOLDOWN_S
    tone: str = "info"
    conf: float = 1.0                          # confidence of the data behind it (minimap facts < API)
    ttl: float = 20.0                          # validity window once shown (s), re-checked every tick

    def applies(self, c: TipContext) -> bool:
        if self.roles is not None and (c.role or "") not in self.roles:
            return False
        try:
            return bool(self.when(c))
        except Exception:
            return False

    def render(self, c: TipContext) -> str:
        try:
            return self.text.format(**c.fmt())
        except (KeyError, IndexError, ValueError):
            return self.text

    def utility(self) -> float:
        """urgency x relevance x confidence."""
        relevance = 1.0 if (self.roles is not None or self.prio >= 3) else 0.8
        return PRIO_URGENCY.get(self.prio, 0.2) * relevance * self.conf


LANERS = ("TOP", "MIDDLE", "BOTTOM", "UTILITY")
CARRIES = ("TOP", "MIDDLE", "BOTTOM")
BOTSIDE = ("MIDDLE", "BOTTOM", "UTILITY", "JUNGLE")
TOPSIDE = ("TOP", "JUNGLE", "MIDDLE")
MAP = 0.8                                     # confidence of minimap-derived facts
T = Tip

# Every text: WHAT to do, " : ", WHY - at most 12 words, plain French (no jargon).
TIPS: tuple[Tip, ...] = (
    # ------------------------------------------------------------------ immediate danger / numbers
    T("outnumbered", "survival", "Recule tout de suite : {en} ennemis contre {al} autour de toi",
      lambda c: not c.in_base and not c.dead and c.enemies_near >= 2 and c.enemies_near - c.allies_near >= 2,
      prio=4, tone="danger", conf=MAP, cooldown=30.0, ttl=6.0),
    T("outnumber", "macro", "Lance le combat : vous êtes {al} contre {en}",
      lambda c: not c.in_base and not c.dead and c.enemies_near >= 1 and c.allies_near - c.enemies_near >= 2
      and c.missing <= 1 and (c.hp is None or c.hp >= 0.5),
      prio=3, tone="go", conf=MAP, cooldown=45.0, ttl=6.0),
    T("missing_3", "map", "Recule vers ta tour : {missing} ennemis invisibles",
      lambda c: c.missing >= 3 and not c.in_base and not c.dead and c.gt >= 240, roles=LANERS, prio=4,
      tone="danger", conf=MAP, cooldown=90.0, ttl=10.0),
    # a side laner farming his lane at 15:00 is doing his job: only from 20:00, 3+ unseen, nobody with me
    T("side_late", "map", "Reviens vers ton équipe : seul {my_lane}, {missing} ennemis invisibles",
      lambda c: c.gt >= 1200 and c.side_lane and c.missing >= 3 and c.allies_near <= 1 and not c.in_base
      and not c.dead, prio=4, tone="danger", conf=MAP, cooldown=60.0, ttl=10.0),
    T("low_hp", "survival", "Recule puis rentre en base : {hp_pct} % de vie",
      lambda c: c.hp is not None and c.hp < 0.3 and not c.in_base and not c.dead, prio=4, tone="danger",
      cooldown=45.0, ttl=8.0),
    T("deaths_many", "survival", "Joue près de ta tour : {rd} morts ces 5 dernières minutes",
      lambda c: c.recent_deaths >= 2 and not c.dead, prio=3, tone="warning", cooldown=240.0),
    # ------------------------------------------------------------------ enemy jungler
    T("jg_coming", "jungle", "Reste près de ta tour : {jg} rôde {jg_last}",
      lambda c: c.jg_last_side == c.lane and c.lane in ("top", "bot") and c.jg_hidden_s is not None
      and c.jg_hidden_s < 40 and not c.jg_dead and c.gt >= 150, roles=LANERS, prio=4, tone="warning", conf=MAP,
      cooldown=90.0, ttl=12.0),
    T("jg_far", "jungle", "Mets la pression sur {opp} : {jg} est loin, {jg_last}",
      lambda c: c.jg_last_side in ("top", "bot") and c.lane in ("top", "bot", "mid")
      and c.jg_last_side != c.lane and c.jg_hidden_s is not None and c.jg_hidden_s < 25 and c.gt >= 150
      and not c.opp_dead and not c.jg_dead and (c.hp is None or c.hp >= 0.5),
      roles=LANERS, prio=3, tone="go", conf=MAP, cooldown=120.0, ttl=12.0),
    T("jg_unseen", "jungle", "Ne t'avance pas : {jg} invisible depuis {jg_h} s",
      lambda c: c.jg_hidden_s is not None and c.jg_hidden_s >= 60 and 180 <= c.gt and c.early and not c.jg_dead
      and c.jg_last_side is not None,
      roles=LANERS, prio=3, tone="warning", conf=MAP, cooldown=150.0),
    # never seen yet: no seconds count (it would contradict the "pas encore vu" JGL line of the HUD)
    T("jg_never_seen", "jungle", "Ne t'avance pas : {jg} pas encore vu",
      lambda c: c.jg_hidden_s is not None and c.jg_hidden_s >= 60 and 180 <= c.gt and c.early and not c.jg_dead
      and c.jg_last_side is None and not c.jg_visible,
      roles=LANERS, prio=3, tone="warning", conf=MAP, cooldown=150.0),
    T("jg_level3", "jungle", "Balise ta rivière avant 2:40 : {jg} peut ganker",
      lambda c: 115 <= c.gt <= 165 and not c.plan_jg, roles=LANERS, prio=3, tone="warning"),
    T("jg_counter", "jungle", "Prends ses camps {jg_opp_side} : {jg} est {jg_side}",
      lambda c: c.jg_visible and c.jg_side in ("top", "bot"), roles=("JUNGLE",), prio=3, tone="go", conf=MAP,
      cooldown=120.0, ttl=10.0),
    T("jg_dead_window", "jungle", "Prends {jgobj} maintenant : {jg} est mort",
      lambda c: c.jg_dead and c.jg_objective() is not None, prio=4, tone="go", cooldown=60.0, ttl=10.0),
    T("jg_dead_lane", "jungle", "Joue agressif : {jg} est mort, pas de gank",
      lambda c: c.jg_dead and c.early and not c.alive, roles=LANERS, prio=3, tone="go", cooldown=60.0, ttl=10.0),
    # ------------------------------------------------------------------ objectives
    T("drag_buy", "objectives", "Rentre acheter une balise de contrôle : dragon dans {drag_s} s",
      lambda c: c.soon_within("dragon", 50, 100) and not c.has_control_ward and not c.in_base,
      roles=BOTSIDE, prio=4, tone="warning", ttl=15.0),
    T("drag_prio", "objectives", "Pousse ta vague puis va au dragon : apparition dans {drag_s} s",
      lambda c: c.soon_within("dragon", 25, 75), roles=("MIDDLE", "BOTTOM", "UTILITY"), prio=3, tone="info",
      ttl=15.0),
    T("drag_vision", "objectives", "Balise la rivière du bas : dragon dans {drag_s} s",
      lambda c: c.soon_within("dragon", 20, 80), roles=("UTILITY", "JUNGLE"), prio=3, ttl=15.0),
    T("drag_top", "objectives", "Pousse et frappe leur tour : leur équipe regarde le dragon",
      lambda c: c.soon_within("dragon", 0, 90) and c.early and c.wave == "pushing" and not c.jg_on_me
      and c.jg_last_side == "bot" and c.jg_hidden_s is not None and c.jg_hidden_s < 25,
      roles=("TOP",), prio=3, tone="go", ttl=15.0),
    T("drag_smite", "objectives", "Garde ton Châtiment pour la fin : dragon dans {drag_s} s",
      lambda c: c.soon_within("dragon", 0, 60), roles=("JUNGLE",), prio=3, ttl=15.0),
    T("drag_up_team", "objectives", "Va au dragon avec ton équipe : {tgd} d'or d'avance",
      lambda c: "dragon" in c.alive and c.team_gold_diff >= 1500, prio=3, tone="go"),
    T("drag_up_behind", "objectives", "Dragon seulement avec des balises posées : {tgd} d'or de retard",
      lambda c: "dragon" in c.alive and c.team_gold_diff <= -1500, prio=3, tone="warning"),
    T("elder", "objectives", "Ne meurs pas avant l'ancestral : il décide la partie",
      lambda c: c.soon_within("elder", 0, 90) or "elder" in c.alive, prio=4, tone="warning"),
    T("baron_soon", "objectives", "Balise autour du Baron : il apparaît dans {baron_s} s",
      lambda c: c.soon_within("baron", 20, 90), prio=3, ttl=15.0),
    T("baron_unknown", "objectives", "Pas de Baron sans voir {jg} : il peut le voler",
      lambda c: "baron" in c.alive and not c.jg_visible and not c.jg_dead and not c.dead_names, prio=3,
      tone="warning", conf=MAP),
    # 2 dead is not a Baron window unless their timers cover the walk + the kill
    T("baron_window", "objectives", "Va au Baron avec ton équipe : {n_dead} ennemis morts",
      lambda c: "baron" in c.alive and len(c.dead_names) >= 2 and c.gt >= 1200
      and c.dead_respawn >= (25 if len(c.dead_names) >= 3 else 40), prio=4, tone="go", ttl=10.0),
    T("herald_soon", "objectives", "Pousse ta vague puis aide au Héraut : {herald_s} s",
      lambda c: c.soon_within("herald", 20, 90), roles=TOPSIDE, prio=3, ttl=15.0),
    T("herald_up", "objectives", "Aide ton jungler au Héraut : il détruit une tour",
      lambda c: "herald" in c.alive and c.early and c.wave != "pushed_in", roles=("TOP", "MIDDLE"), prio=2),
    T("grubs", "objectives", "Pousse ta vague puis aide aux larves : {grubs_s} s",
      lambda c: c.soon_within("grubs", 10, 80), roles=TOPSIDE, prio=3, ttl=15.0),
    T("group_obj", "macro", "Rejoins ton équipe vers {my_le} : {my_obj_s} s",
      lambda c: not c.early and c.my_objective(10, 60) is not None and not c.in_base and not c.dead,
      prio=3, ttl=15.0),
    # TP for the fight on the OTHER side of the map (a top laner is already next to Baron / Herald)
    T("tp_obj", "macro", "Garde ta Téléportation pour le dragon : {drag_s} s",
      lambda c: c.gt >= 600 and c.has_tp and c.soon_within("dragon", 10, 70), roles=("TOP",), prio=3, ttl=15.0),
    # 2026 top role quest: completing it gives a free Teleport (or upgrades the one taken)
    T("tp_quest_obj", "macro", "Téléporte-toi au dragon : {drag_s} s",
      lambda c: c.gt >= 900 and not c.has_tp and c.soon_within("dragon", 10, 70), roles=("TOP",), prio=2,
      ttl=15.0),
    T("sup_obj_vision", "vision", "Va baliser {next_le} : apparition dans {next_s} s",
      lambda c: c.next_objective(90) is not None and (c.next_objective(90) or ("", 0))[1] >= 30,
      roles=("UTILITY",), prio=3, ttl=15.0),
    # ------------------------------------------------------------------ lane matchup
    T("opp_dead", "matchup", "Pousse ta vague et tape la tour : {opp} est mort",
      lambda c: c.opp_dead and c.early and not c.jg_on_me, roles=CARRIES, prio=4, tone="go", cooldown=60.0,
      ttl=10.0),
    T("opp_dead_late", "matchup", "Prends la tour puis rejoins ton équipe : {opp} est mort",
      lambda c: c.opp_dead and not c.early and not c.jg_on_me, roles=CARRIES, prio=4, tone="go", cooldown=60.0,
      ttl=10.0),
    T("lvl_ahead", "matchup", "Va taper {opp} : tu as {lvl_txt} d'avance",
      lambda c: c.level_diff >= 1 and c.early and not c.jg_on_me and (c.hp is None or c.hp >= 0.5),
      roles=LANERS, prio=3, tone="go", cooldown=180.0),
    T("lvl_behind", "matchup", "Joue prudemment sous ta tour : {opp} a {lvl_txt} d'avance",
      lambda c: c.level_diff <= -1 and c.early, roles=LANERS, prio=3, tone="warning", cooldown=180.0),
    # power spikes (spikes.SpikeTracker): the first to reach 2 / 3 / 6 / 11 / 16 (the opponent's 11 / 16 and
    # the big items are announced by the Tab insights / praise; all of them move the play gauge)
    T("spike_me_big", "matchup", "Attaque {opp} : tu es {sp_lvl} avant lui",
      lambda c: c.spike_who == "me" and c.spike_what == "level" and c.spike_level in (2, 6) and not c.in_base
      and not c.dead and not c.opp_dead, roles=LANERS, prio=4, tone="go", cooldown=60.0, ttl=12.0),
    T("spike_me", "matchup", "Joue plus fort : tu es {sp_lvl} avant {opp}",
      lambda c: c.spike_who == "me" and c.spike_what == "level" and c.spike_level in (3, 11, 16)
      and not c.in_base and not c.dead and not c.opp_dead, roles=LANERS, prio=3, tone="go", cooldown=60.0,
      ttl=12.0),
    T("spike_opp_big", "matchup", "Recule : {opp} est {sp_lvl}, pas toi",
      lambda c: c.spike_who == "opp" and c.spike_what == "level" and c.spike_level in (2, 3, 6) and not c.in_base
      and not c.dead and not c.opp_dead, roles=LANERS, prio=4, tone="warning", cooldown=60.0, ttl=12.0),
    T("gold_ahead", "matchup", "Prends les plaques de {opp} : {gd} d'or d'avance",
      lambda c: c.gold_diff >= 800 and c.early, roles=LANERS, prio=3, tone="go"),
    T("gold_behind", "matchup", "Farme sans combattre : {opp} a {gd} d'or d'avance",
      lambda c: c.gold_diff <= -800, roles=LANERS, prio=3, tone="warning"),
    T("cs_behind_opp", "matchup", "Concentre-toi sur les sbires : {cs_gap} de retard sur {opp}",
      lambda c: c.cs_diff <= -15, roles=CARRIES, prio=3, tone="warning"),
    T("fed_enemy", "matchup", "Évite {fed} en un contre un : il est trop fort",
      lambda c: bool(c.fed), prio=3, tone="warning"),
    T("level2", "matchup", "Tue vite la première vague : le premier niveau 2 gagne",
      lambda c: 30 <= c.gt <= 85, roles=LANERS, prio=3),
    # 2026: plates stay all game, but outer plates lose 10 gold per minute from 11:00 (-40 at 15:00)
    T("plates_decay", "matchup", "Prends les plaques avant 11:00 : ensuite elles valent moins",
      lambda c: 540 <= c.gt < 660 and c.wave in ("pushing", None), roles=LANERS, prio=3, tone="go"),
    # ------------------------------------------------------------------ farm
    T("cs_low", "farm", "Reste sur ta vague : {cspm} sbires/min, vise {target}",
      lambda c: c.gt >= 300 and c.cs_target > 0 and c.cspm < c.cs_target - 1.0, roles=CARRIES, prio=2),
    T("jg_cs", "farm", "Enchaîne tes camps entre deux ganks : {cspm}/min, vise {target}",
      lambda c: c.gt >= 300 and c.cspm < c.cs_target - 0.8, roles=("JUNGLE",), prio=3),
    T("cs_side", "farm", "Prends les sbires sur un côté : aucun objectif avant 1 min",
      lambda c: (c.mid or c.late) and not any(r <= 60 for r in c.soon.values()) and not c.alive
      and c.missing < 2, roles=CARRIES, prio=2),
    T("cs_under_tower", "farm", "Laisse ta tour taper : puis achève le sbire",
      lambda c: c.wave == "pushed_in" and c.early, roles=CARRIES, prio=2, conf=MAP),
    # ------------------------------------------------------------------ waves
    T("wave_push_back", "wave", "Pousse ta vague puis rentre : {gold} d'or à dépenser",
      lambda c: c.wave == "pushing" and c.gold >= 1100 and not c.in_base and not c.recall_said
      and c.my_objective(0, 50) is None, roles=CARRIES, prio=3, conf=MAP),
    T("wave_push_ward", "wave", "Balise la rivière : ta vague pousse, tu es exposé",
      lambda c: c.wave == "pushing" and not c.jg_visible and c.early and c.gt >= 150, roles=LANERS, prio=2,
      tone="warning",
      conf=MAP),
    T("wave_pushed_in", "wave", "Prends les sbires sous ta tour : la vague revient",
      lambda c: c.wave == "pushed_in" and c.early, roles=LANERS, prio=2, conf=MAP),
    T("wave_hold", "wave", "Garde la vague près de ta tour : {opp} devra s'avancer",
      lambda c: c.gold_diff >= 500 and c.early and c.wave == "pushed_in", roles=("TOP", "BOTTOM"), prio=2,
      conf=MAP),
    T("mid_roam", "macro", "Va aider top ou bot : ta vague est poussée",
      lambda c: 240 <= c.gt <= 1200 and c.wave == "pushing", roles=("MIDDLE",), prio=2, conf=MAP),
    # ------------------------------------------------------------------ vision
    T("vis_river", "vision", "Pose ta balise dans la rivière : premier gank vers 2:30",
      lambda c: 45 <= c.gt <= 120, roles=LANERS, prio=2),
    # 2026 Faelights ("lampes féeriques"): a ward on one gets +25 % vision and reveals an area 45 s
    T("vis_faelight", "vision", "Pose ta balise sur une lampe féerique : vision bonus 45 s",
      lambda c: 90 <= c.gt <= 900 and not c.in_base and not c.dead, roles=("UTILITY", "JUNGLE", "MIDDLE"),
      prio=2, cooldown=300.0),
    T("vis_control_base", "vision", "Achète une balise de contrôle (75 or) : elle révèle leurs balises",
      lambda c: c.in_base and not c.has_control_ward and c.gt >= 240, prio=3),
    T("vis_sweeper", "vision", "Passe au Balayeur : il enlève leurs balises avant les objectifs",
      lambda c: c.gt >= 600 and SWEEPER not in c.items and c.in_base, roles=("UTILITY", "JUNGLE"), prio=3),
    T("vis_score_low", "vision", "Pose ta balise dès qu'elle est prête : score de vision {ward}",
      lambda c: c.gt >= 600 and c.ward_score < c.minute * 0.6 and not c.in_base, prio=2),
    T("vis_mid_side", "vision", "Balise la rivière {jg_last} : {jg} y était",
      lambda c: c.early and c.jg_last_side in ("top", "bot") and c.jg_hidden_s is not None
      and 10 <= c.jg_hidden_s <= 60, roles=("MIDDLE",), prio=2, conf=MAP),
    T("vis_top_bush", "vision", "Balise le buisson de la rivière : ganks par derrière",
      lambda c: 150 <= c.gt <= 480, roles=("TOP",), prio=2),
    T("vis_bot_bush", "vision", "Garde le buisson de ta voie : il cache leurs attaques",
      lambda c: 90 <= c.gt <= 480, roles=("BOTTOM", "UTILITY"), prio=2),
    T("vis_deep", "vision", "Balise leur jungle : tu sauras où va {jg}",
      lambda c: c.gt >= 600 and not c.alive, roles=("UTILITY", "JUNGLE"), prio=2),
    # ------------------------------------------------------------------ items / gold
    # in the shop: name what the gold buys NOW (components), never a 3000-gold legendary with 900 gold
    T("buy_item", "items", "Achète {buy} maintenant : tu as l'or",
      lambda c: c.in_base and bool(c.buy_names) and c.gold >= 300, prio=4, tone="go", ttl=12.0),
    T("comp_ready", "items", "Rentre acheter {buy} : tu as l'or",
      lambda c: bool(c.buy_names) and c.buy_value >= 700 and not c.in_base and not c.dead and c.missing < 3
      and c.enemies_near == 0 and c.my_objective(0, 50) is None and not c.recall_said, prio=3, cooldown=150.0,
      ttl=15.0),
    T("gold_back", "items", "Rentre acheter : {gold} d'or, ça fait un objet",
      lambda c: c.gold >= 1300 and not c.buy_names and not c.in_base and not c.dead and c.missing < 3
      and c.my_objective(0, 50) is None and not c.recall_said, prio=3, cooldown=150.0),
    # objective timing: recall now to be back in time / don't recall right before it
    T("obj_recall_now", "objectives", "Rentre maintenant : tu reviendras à temps pour {my_le}",
      lambda c: c.my_objective(75, 120) is not None and not c.in_base and not c.dead and c.enemies_near == 0
      and (c.gold >= 900 or (c.hp is not None and c.hp < 0.6)), prio=3, cooldown=240.0, ttl=15.0),
    T("obj_stay", "objectives", "Ne rentre pas : {my_obj} dans {my_obj_s} s, reste prêt",
      lambda c: c.my_objective(10, 50) is not None and not c.in_base and not c.dead and c.gold >= 1100
      and (c.hp is None or c.hp >= 0.5), prio=3, tone="warning", cooldown=240.0, ttl=12.0),
    # power plays (phase.MapState)
    T("soul_us", "objectives", "Prends ce dragon avec ton équipe : c'est l'âme",
      lambda c: c.soul == "us" and (c.soon_within("dragon", 0, 90) or "dragon" in c.alive), prio=4, tone="go",
      cooldown=240.0, ttl=15.0),
    T("soul_them", "objectives", "Va au dragon avec ton équipe : sinon ils ont l'âme",
      lambda c: c.soul == "them" and (c.soon_within("dragon", 0, 90) or "dragon" in c.alive), prio=4,
      tone="warning", cooldown=240.0, ttl=15.0),
    T("baron_us", "macro", "Pousse une voie avec ton équipe : Baron encore {baron_left} s",
      lambda c: c.baron_buff_s >= 20 and not c.dead, prio=3, tone="go", cooldown=90.0, ttl=15.0),
    T("baron_them", "macro", "Défends sous tes tours : Baron ennemi encore {ebaron} s",
      lambda c: c.enemy_baron_s >= 20 and not c.dead, prio=3, tone="warning", cooldown=90.0, ttl=15.0),
    T("elder_us", "macro", "Attaque avec ton équipe : ancestral encore {elder_left} s",
      lambda c: c.elder_buff_s >= 15 and not c.dead, prio=4, tone="go", cooldown=90.0, ttl=12.0),
    T("elder_them", "macro", "Évite le combat : ils ont l'ancestral {eelder} s",
      lambda c: c.enemy_elder_s >= 15 and not c.dead, prio=4, tone="danger", cooldown=90.0, ttl=12.0),
    # session goal (goals.GoalTracker)
    T("goal_deaths", "survival", "Joue prudent : encore une mort et ton objectif est raté",
      lambda c: c.goal_risk == "last_death" and not c.dead and not c.in_base, prio=3, tone="warning",
      cooldown=600.0, ttl=12.0),
    T("goal_cs", "farm", "Reste sur ta vague : objectif {goal_t} sbires/min, tu es à {cspm}",
      lambda c: c.goal_risk == "cs_behind" and not c.dead, roles=CARRIES, prio=3, cooldown=240.0, ttl=12.0),
    # game-start plan (game_plan.matchup_card)
    T("plan_lane", "matchup", "{plan1}", lambda c: bool(c.plan1) and 20 <= c.gt <= 150, prio=2,
      cooldown=9999.0, ttl=15.0),
    T("plan_lane2", "matchup", "{plan2}", lambda c: bool(c.plan2) and 40 <= c.gt <= 160, prio=2,
      cooldown=9999.0, ttl=15.0),
    T("plan_jg", "jungle", "{plan_jg}", lambda c: bool(c.plan_jg) and 110 <= c.gt <= 190, roles=LANERS,
      prio=3, tone="warning", cooldown=9999.0, ttl=15.0),
    T("gold_spend", "items", "Dépense tes {gold} d'or : l'or gardé ne sert à rien",
      lambda c: c.in_base and c.gold >= 500, prio=3, ttl=10.0),
    T("boots", "items", "Achète des bottes : plus rapide, tu évites les ganks",
      lambda c: c.gt >= 600 and not c.has_boots, roles=CARRIES, prio=3),
    T("defensive", "items", "Prends un objet défensif : {rd} morts en 5 minutes",
      lambda c: c.recent_deaths >= 2 and c.gt >= 600 and c.in_base, prio=3),
    T("sup_quest", "items", "Finis ta quête de support : elle débloque tes balises",
      lambda c: 120 <= c.gt <= 600, roles=("UTILITY",), prio=2),
    T("elixir", "items", "Prends un élixir avant le combat : gros bonus 3 minutes",
      lambda c: c.late and c.gold >= 500 and c.in_base, prio=2),
    T("first_back", "items", "Farme jusqu'à 1100 d'or : puis rentre en base",
      lambda c: 240 <= c.gt <= 420 and 600 <= c.gold < 1100 and not c.in_base, roles=LANERS, prio=2),
    # ------------------------------------------------------------------ death / respawn
    T("dead_obj", "survival", "Va vers {my_le} en réapparaissant : {my_obj_s} s",
      lambda c: c.dead and c.my_objective(0, 90) is not None, prio=4, cooldown=120.0, ttl=15.0),
    T("dead_watch", "survival", "Regarde la carte pendant ta mort : où sont les ennemis",
      lambda c: c.dead, prio=3, cooldown=240.0, ttl=15.0),
    # ------------------------------------------------------------------ team / macro
    T("ahead_team", "macro", "Frappe les tours avec ton équipe : {tgd} d'or d'avance",
      lambda c: c.team_gold_diff >= 3000 and not c.early, prio=3, tone="go"),
    T("behind_team", "macro", "Défends sous tes tours : {tgd} d'or de retard",
      lambda c: c.team_gold_diff <= -3000, prio=3, tone="warning"),
    T("mid_transition", "phase", "Rejoins ton équipe au milieu : fin de la phase de voie",
      lambda c: 840 <= c.gt <= 960 and c.my_objective(0, 90) is None and not c.alive, roles=LANERS, prio=2),
    T("late_vision", "phase", "Avance seulement derrière une balise : sinon embuscade",
      lambda c: c.late and not c.in_base, prio=2),
    T("teamfight_adc", "macro", "Tape le plus proche en combat : reste derrière ton tank",
      lambda c: not c.early, roles=("BOTTOM",), prio=2),
    T("teamfight_sup", "macro", "Reste collé à ton tireur en combat : protège-le",
      lambda c: not c.early, roles=("UTILITY",), prio=2),
    T("split_top", "macro", "Pousse ta voie : presque tous les ennemis sont visibles",
      lambda c: (c.mid or c.late) and c.side_lane and c.missing <= 1 and c.enemies_near == 0
      and (c.jg_visible or c.jg_dead) and not any(r <= 60 for r in c.soon.values()), roles=("TOP",), prio=2,
      conf=MAP),
    T("jg_gank", "macro", "Va ganker une voie poussée : l'ennemi est loin de sa tour",
      lambda c: 180 <= c.gt <= 840, roles=("JUNGLE",), prio=2),
    T("jg_scuttle", "macro", "Prends le Carapateur : ta voie forte peut t'aider",
      lambda c: 170 <= c.gt <= 235, roles=("JUNGLE",), prio=3),    # scuttles at 2:55 (2026)
    T("early_safe", "phase", "Ne donne pas le premier sang : reste près de ta tour",
      lambda c: 30 <= c.gt <= 180 and c.level_diff <= 0, roles=LANERS, prio=1),
)


def tip_count() -> int:
    return len(TIPS)


def _player_names(p: Any) -> set[str]:
    out = set()
    for attr in ("riot_id", "summoner_name"):
        v = str(getattr(p, attr, "") or "").strip().casefold()
        if v:
            out.add(v)
            out.add(v.split("#", 1)[0])
    return out


def build_context(facts: dict[str, Any] | None, game: Any, scoreboard: Any = None, stance: Any = None,
                  item: str | None = None, extra: dict[str, Any] | None = None) -> TipContext:
    """:class:`TipContext` from the coach facts + Live Client data + Tab summary (+ ``extra`` fields
    from :meth:`treeaicoach.coach_plus.CoachPlus.tip_fields`). Never raises."""
    c = TipContext()
    for k, v in (extra or {}).items():
        if hasattr(c, k) and not k.startswith("_") and v is not None:
            try:
                setattr(c, k, v)
            except Exception:
                pass
    try:
        f = facts or {}
        me = getattr(game, "me", None)
        c.gt = _f(f.get("gt"), None) or _f(getattr(game, "game_time", None), 0.0) or 0.0
        role = f.get("my_role") or str(getattr(me, "position", "") or "").upper() or None
        c.role = role if role in ROLES else None
        c.lane = f.get("role_lane")
        c.my_lane = f.get("my_lane") if f.get("my_lane") in ("top", "mid", "bot") else None
        c.item = item or None
        nums = f.get("numbers")
        if isinstance(nums, (tuple, list)) and len(nums) == 2:
            c.enemies_near = int(_f(nums[0], 0) or 0)
            c.allies_near = max(1, int(_f(nums[1], 1) or 1))
        c.dead = bool(getattr(me, "is_dead", False))
        spells = [str(x).casefold() for x in tuple(getattr(me, "spell_ids", ()) or ()) + tuple(getattr(me, "spells", ()) or ())]
        if spells:
            c.has_tp = any("teleport" in x or "téléport" in x for x in spells)
        c.in_base = bool(f.get("in_base"))
        c.safe = bool(f.get("safe"))
        c.level = int(_f(getattr(me, "level", None), 1) or 1)
        scores = getattr(me, "scores", None) or {}
        c.kills = int(_f(scores.get("kills"), 0) or 0)
        c.deaths = int(_f(scores.get("deaths"), 0) or 0)
        c.assists = int(_f(scores.get("assists"), 0) or 0)
        c.cs = int(_f(scores.get("creepScore"), 0) or 0)
        c.cspm = round(c.cs / (c.gt / 60.0), 1) if c.gt >= 60 else 0.0
        c.cs_target = CS_TARGET.get(c.role or "", 7.0)
        c.ward_score = float(_f(scores.get("wardScore"), 0.0) or 0.0)
        c.gold = float(_f(getattr(game, "current_gold", None), 0.0) or 0.0)
        c.items = frozenset(int(i) for i in (getattr(me, "items", None) or [])
                            if isinstance(i, int) and not isinstance(i, bool))
        stats = getattr(game, "champion_stats", None) or {}
        cur, mx = _f(stats.get("currentHealth")), _f(stats.get("maxHealth"))
        c.hp = max(0.0, min(1.0, cur / mx)) if cur is not None and mx else None
        opps = f.get("opponents") or []
        if opps:
            c.opp = str(opps[0].get("name") or "") or None
            c.opp_dead = any(o.get("dead") for o in opps)
            lv = _f(opps[0].get("level"))
            c.opp_level = int(lv) if lv else None
        c.dead_names = tuple(str(getattr(p, "champion_name", "") or getattr(p, "champion_alias", "") or "")
                             for p in (getattr(game, "enemies", None) or [])
                             if bool(getattr(p, "is_dead", False)))
        resp = [_f(getattr(p, "respawn_timer", None), 0.0) or 0.0 for p in (getattr(game, "enemies", None) or [])
                if bool(getattr(p, "is_dead", False))]
        c.dead_respawn = float(min(resp)) if resp else 0.0
        m = getattr(scoreboard, "my_matchup", None) if scoreboard is not None else None
        if m is not None:
            c.opp = m.enemy or c.opp
            c.level_diff, c.gold_diff, c.cs_diff = int(m.level_diff), int(m.gold_diff), int(m.cs_diff)
        elif opps and opps[0].get("level"):
            c.level_diff = c.level - int(opps[0]["level"])
        if scoreboard is not None and getattr(scoreboard, "players", None):
            c.team_gold_diff = int(scoreboard.team_gold_diff)
            names = {p.alias: p.name for p in scoreboard.players}
            c.fed = tuple(names.get(a, a) for a in scoreboard.fed)
        for o in f.get("objectives") or []:
            key = str(o.get("key") or "")
            if not key:
                continue
            c.obj_names[key] = o.get("name") or key
            if o.get("alive"):
                c.alive = c.alive | {key}
            else:
                rem = _f(o.get("remaining"))
                if rem is not None and 0 <= rem <= 120:
                    c.soon[key] = rem
        c.wave = f.get("wave")
        jg = f.get("jungler") or {}
        c.jg = jg.get("name")
        c.jg_visible = bool(jg.get("visible"))
        c.jg_side = jg.get("side")
        c.jg_last_side = jg.get("last_side")
        c.jg_hidden_s = _f(jg.get("hidden_s"))
        c.jg_dead = bool(jg.get("dead"))
        c.missing = int(_f(f.get("missing"), 0) or 0)
        c.stance = getattr(stance, "level", None) if stance is not None else None
        names = _player_names(me)
        n = 0
        for e in getattr(game, "events", None) or []:
            if isinstance(e, dict) and e.get("EventName") == "ChampionKill":
                v = str(e.get("VictimName") or "").strip().casefold()
                tt = _f(e.get("EventTime"), None)
                if (v in names or v.split("#", 1)[0] in names) and tt is not None and c.gt - tt <= 300:
                    n += 1
        c.recent_deaths = n
    except Exception:
        log.debug("tips.build_context failed", exc_info=True)
    return c


HOLD_S = 10.0                   # the shown advice stays at least this long ...
PREEMPT_GAIN = 0.2              # ... unless a new one is this much more useful
STALE_GRACE_S = 1.5             # a tip that stops applying disappears after this grace


class TipRotator:
    """The single most useful written advice right now (see the module docstring).

    Ranking: :meth:`Tip.utility` (urgency x relevance x confidence); generic tips (prio <= 2)
    are dropped whenever a contextual one (prio >= 3) applies. The shown tip is held at least
    :data:`HOLD_S`, replaced sooner by one :data:`PREEMPT_GAIN` more useful, removed as soon as it
    stops applying (stale advice is never shown) and rotated after ``rotate_s`` / its ``ttl``
    (then on cooldown) so the next most useful one gets its turn."""

    def __init__(self, seed: int | None = None, rotate_s: float = ROTATE_S, tips: tuple[Tip, ...] = TIPS) -> None:
        self._lock = threading.Lock()
        self._rng = random.Random(seed)
        self.rotate_s = float(rotate_s)
        self._tips = tuple(tips)
        self.min_prio = 1                  # skill level: only tips with prio >= this (skill.py)
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._current: Tip | None = None
            self._text: str | None = None
            self._since: float | None = None
            self._stale_since: float | None = None
            self._shown_gt: dict[str, float] = {}       # tip id -> game time shown
            self._last_cat: str | None = None
            self._last_gt: float | None = None
            self._history: list[str] = []

    def current(self) -> str | None:
        with self._lock:
            return self._text

    def current_id(self) -> str | None:
        with self._lock:
            return self._current.id if self._current is not None else None

    def current_tip(self) -> Tip | None:
        with self._lock:
            return self._current

    def since(self) -> float | None:
        with self._lock:
            return self._since

    def history(self) -> list[str]:
        with self._lock:
            return list(self._history)

    def update(self, t: float, ctx: TipContext) -> str | None:
        """Current tip text. Never raises."""
        try:
            with self._lock:
                return self._update_locked(float(t), ctx)
        except Exception:
            log.exception("TipRotator.update failed")
            return None

    def _show(self, tip: Tip, t: float, gt: float, ctx: TipContext) -> str | None:
        self._current, self._since, self._stale_since = tip, t, None
        self._last_cat = tip.category
        self._shown_gt[tip.id] = gt
        self._text = tip.render(ctx)
        self._history.append(tip.id)
        del self._history[:-200]
        return self._text

    def _update_locked(self, t: float, ctx: TipContext) -> str | None:
        gt = float(ctx.gt)
        if self._last_gt is not None and gt < self._last_gt - 5.0:     # new game / loop
            self._shown_gt.clear()
            self._current = None
            self._since = None
            self._text = None
        self._last_gt = gt
        cur = self._current
        best = self._pick(ctx, gt)
        if cur is not None and self._since is not None:
            age = t - self._since
            if age < 0:
                self._since, age = t, 0.0
            still = cur.applies(ctx)
            if not still:
                self._stale_since = t if self._stale_since is None else self._stale_since
                if t - self._stale_since >= STALE_GRACE_S:
                    self._current, self._text, self._since = None, None, None
                    return self._show(best, t, gt, ctx) if best is not None else None
            else:
                self._stale_since = None
            if self._current is cur:
                preempt = best is not None and best.utility() >= cur.utility() + PREEMPT_GAIN and age >= 1.0
                limit = min(self.rotate_s, max(cur.ttl, HOLD_S))
                # after its validity window the next most useful tip gets its turn (an urgent tip
                # that still applies is only replaced by another urgent one)
                rotate = (age >= limit and best is not None
                          and not (still and cur.prio >= 4 and best.prio < 4))
                if not preempt and not rotate:
                    if still:
                        self._text = cur.render(ctx)       # numbers in the text stay fresh
                    return self._text
        if best is None:
            if cur is not None and not cur.applies(ctx):
                self._current, self._text = None, None
            return self._text
        return self._show(best, t, gt, ctx)

    def candidates(self, ctx: TipContext) -> list[tuple[float, Tip]]:
        """Every applicable tip with its utility, best first (cooldowns ignored)."""
        out = [(tp.utility(), tp) for tp in self._tips if tp.applies(ctx)]
        out.sort(key=lambda x: -x[0])
        return out

    def _pick(self, ctx: TipContext, gt: float) -> Tip | None:
        cands = []
        for tip in self._tips:
            if self._current is not None and tip.id == self._current.id:
                continue
            last = self._shown_gt.get(tip.id)
            if last is not None and 0.0 <= gt - last < tip.cooldown:
                continue
            if tip.prio < self.min_prio and tip.tone not in ("red",):
                continue
            if ctx.macro_tone == "go" and tip.tone in ("warning", "danger") and tip.prio < 4:
                continue                       # a "go" macro call is on screen: no cautious tip next to it
            if ctx.macro_tone == "danger" and tip.tone == "go":
                continue                       # a "recule" call: never "attaque" next to it
            if tip.applies(ctx):
                cands.append(tip)
        if not cands:
            return None
        if any(tp.prio >= 3 for tp in cands):              # something specific: no generic tip
            cands = [tp for tp in cands if tp.prio >= 3]
        # most useful first; same utility: a different category than the last one, then least shown
        return max(cands, key=lambda tp: (round(tp.utility(), 3), tp.category != self._last_cat,
                                          -self._shown_gt.get(tp.id, -1e9)))


__all__ = ["Tip", "TipContext", "TipRotator", "TIPS", "build_context", "tip_count", "ROTATE_S", "MAX_WORDS"]
