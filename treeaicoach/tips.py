"""Written coaching tips (never spoken): a data-driven library + an anti-repetition rotator.

:data:`TIPS` holds 100+ short, concrete League of Legends tips in French. Each :class:`Tip`
has a condition on a :class:`TipContext` (role, game phase, lane matchup, items, vision score,
CS/min, objective timers, wave state, enemy jungler, deaths...) and a priority: contextual tips
("Dragon dans 45 s : rentre et achète une balise de contrôle") beat generic ones ("Regarde ta
minimap toutes les 5 secondes").

:class:`TipRotator` shows ONE tip at a time in the HUD tip line and changes it every
:data:`ROTATE_S` seconds (sooner when the current tip no longer applies): weighted random among
the best applicable tips, never the same tip again within its cooldown, never two tips of the
same category in a row. :func:`build_context` makes the context from the coach facts
(:meth:`treeaicoach.coach.MapCoach.facts`), the Live Client data and the Tab summary.

Only the minimap facts the coach already uses + the official Live Client API (my own data and
the public Tab scoreboard). Pure Python, thread-safe, never raises from its public API.
"""

from __future__ import annotations

import logging
import math
import random
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

ROTATE_S = 25.0                 # a new tip every 25 s
MIN_SHOW_S = 8.0                # ... but a tip stays at least this long (even if it stops applying)
DEFAULT_COOLDOWN_S = 480.0      # the same tip is not shown again for 8 min (game time)

ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
CS_TARGET = {"TOP": 7.0, "MIDDLE": 7.0, "BOTTOM": 7.5, "JUNGLE": 5.5, "UTILITY": 0.0}
SIDE_FR = {"top": "en haut", "mid": "au milieu", "bot": "en bas"}
LANE_FR = {"top": "top", "mid": "mid", "bot": "bot"}
CONTROL_WARD = 2055
SWEEPER = 3364
FARSIGHT = 3363
BOOTS = frozenset({1001, 3006, 3009, 3020, 3047, 3111, 3117, 3158, 3010, 2422})
SUPPORT_QUEST = frozenset({3865, 3866, 3867, 3869, 3870, 3871, 3876, 3877})


def _f(x: Any, default: float | None = None) -> float | None:
    if x is None or isinstance(x, bool):
        return default
    try:
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        return default
    return v if math.isfinite(v) else default


@dataclass
class TipContext:
    """Everything a tip condition may look at (defaults = unknown / neutral)."""

    gt: float = 0.0
    role: str | None = None              # TOP / JUNGLE / MIDDLE / BOTTOM / UTILITY
    lane: str | None = None              # top / mid / bot (of my role)
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
    opp_dead: bool = False
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
    stance: str | None = None
    recent_deaths: int = 0               # my deaths in the last 5 min
    safe: bool = False

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
    def has_control_ward(self) -> bool:
        return CONTROL_WARD in self.items

    @property
    def has_boots(self) -> bool:
        return bool(BOOTS & self.items)

    def soon_within(self, key: str, lo: float, hi: float) -> bool:
        r = self.soon.get(key)
        return r is not None and lo <= r <= hi

    def obj(self, key: str, default: str) -> str:
        return str(self.obj_names.get(key) or default)

    def fmt(self) -> dict[str, Any]:
        def secs(key: str) -> int:
            r = self.soon.get(key)
            return int(round((r or 0) / 5.0) * 5)
        return {
            "opp": self.opp or "ton adversaire", "jg": self.jg or "le jungler ennemi",
            "cspm": f"{self.cspm:.1f}".replace(".", ","), "target": f"{self.cs_target:g}".replace(".", ","),
            "lane": LANE_FR.get(self.lane or "", "ta voie"), "jg_side": SIDE_FR.get(self.jg_side or "", ""),
            "jg_last": SIDE_FR.get(self.jg_last_side or "", ""),
            "other": SIDE_FR.get({"top": "bot", "bot": "top"}.get(self.jg_last_side or "", ""), ""),
            "drag_s": secs("dragon"), "baron_s": secs("baron"), "herald_s": secs("herald"),
            "grubs_s": secs("grubs"), "atakhan_s": secs("atakhan"), "elder_s": secs("elder"),
            "fed": self.fed[0] if self.fed else "", "lvl": abs(self.level_diff),
            "gold": int(self.gold), "deaths": self.deaths, "missing": self.missing,
            "ward": int(self.ward_score),
        }


@dataclass(frozen=True)
class Tip:
    id: str
    category: str
    text: str                                  # str.format(**ctx.fmt())
    when: Callable[[TipContext], bool] = lambda c: True
    roles: tuple[str, ...] | None = None       # None = every role
    prio: int = 1                              # 1 generic, 2 role / phase, 3 contextual, 4 urgent
    cooldown: float = DEFAULT_COOLDOWN_S

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


LANERS = ("TOP", "MIDDLE", "BOTTOM", "UTILITY")
CARRIES = ("TOP", "MIDDLE", "BOTTOM")
T = Tip

TIPS: tuple[Tip, ...] = (
    # ------------------------------------------------------------------ objectives (contextual)
    T("drag_buy", "objectives", "Dragon dans {drag_s} s : rentre et achète une balise de contrôle",
      lambda c: c.soon_within("dragon", 50, 100) and not c.has_control_ward, prio=4),
    T("drag_prio", "objectives", "Dragon dans {drag_s} s : pousse bot et mid pour avoir la priorité",
      lambda c: c.soon_within("dragon", 30, 75), prio=3),
    T("drag_vision", "objectives", "Dragon dans {drag_s} s : balise la rivière du bas avant d'y aller",
      lambda c: c.soon_within("dragon", 20, 70), prio=3),
    T("drag_jg_smite", "objectives", "Dragon bientôt : garde ton Châtiment pour la fin du dragon",
      lambda c: c.soon_within("dragon", 0, 60) or "dragon" in c.alive, roles=("JUNGLE",), prio=3),
    T("drag_up_team", "objectives", "Dragon dispo et équipe en avance : forcez-le à 5",
      lambda c: "dragon" in c.alive and c.team_gold_diff >= 1500, prio=3),
    T("drag_up_behind", "objectives", "Dragon dispo mais équipe en retard : ne le contestez qu'avec la vision",
      lambda c: "dragon" in c.alive and c.team_gold_diff <= -1500, prio=3),
    T("elder", "objectives", "Ancestral dans {elder_s} s : ne meurs pas avant, c'est la partie",
      lambda c: c.soon_within("elder", 0, 90) or "elder" in c.alive, prio=4),
    T("baron_soon", "objectives", "Baron dans {baron_s} s : place la vision autour de la fosse du haut",
      lambda c: c.soon_within("baron", 20, 90), prio=3),
    T("baron_up", "objectives", "Baron dispo : ne fais pas le Baron sans savoir où est {jg}",
      lambda c: "baron" in c.alive and not c.jg_visible, prio=3),
    T("baron_after_kill", "objectives", "Un ennemi est mort : bonne fenêtre pour le Baron ou une tour",
      lambda c: "baron" in c.alive and (c.opp_dead or c.jg_dead), prio=4),
    T("herald_soon", "objectives", "Héraut dans {herald_s} s : poussez top et mid pour y aller à plusieurs",
      lambda c: c.soon_within("herald", 20, 90), prio=3),
    T("herald_use", "objectives", "Le Héraut fait tomber les plaques : lâche-le sur la tour de ta voie",
      lambda c: "herald" in c.alive and c.early, prio=2),
    T("grubs", "objectives", "Larves dans {grubs_s} s : pousse top et aide ton jungler",
      lambda c: c.soon_within("grubs", 10, 80) and c.role in ("TOP", "JUNGLE", "MIDDLE"), prio=3),
    T("atakhan", "objectives", "Atakhan dans {atakhan_s} s : regroupez-vous du côté de sa fosse",
      lambda c: c.soon_within("atakhan", 0, 90), prio=3),
    T("obj_dead_jg", "objectives", "Leur jungler est mort : prenez un objectif maintenant",
      lambda c: c.jg_dead and (bool(c.alive) or any(r <= 30 for r in c.soon.values())), prio=4),
    T("obj_trade", "objectives", "Ils font un objectif ? Prenez-en un de l'autre côté de la carte",
      lambda c: c.mid or c.late, prio=1),
    T("obj_after_fight", "objectives", "Après un combat gagné : objectif, tour ou retour en base, pas de chasse",
      lambda c: c.gt > 900, prio=1),
    # ------------------------------------------------------------------ enemy jungler (contextual)
    T("jg_did_bot", "jungle", "{jg} vient de se montrer {jg_last} : son prochain gank est probablement {other}",
      lambda c: c.jg_last_side in ("top", "bot") and not c.jg_visible and c.jg_hidden_s is not None
      and 20 <= c.jg_hidden_s <= 90 and c.lane is not None and c.lane != c.jg_last_side, prio=3),
    T("jg_same_side", "jungle", "{jg} est de ton côté : reste près de ta tour jusqu'à ce qu'il se montre",
      lambda c: c.jg_last_side == c.lane and c.lane in ("top", "bot") and c.jg_hidden_s is not None
      and c.jg_hidden_s < 40, roles=LANERS, prio=4),
    T("jg_unseen", "jungle", "{jg} invisible depuis longtemps : joue comme s'il était dans ta rivière",
      lambda c: c.jg_hidden_s is not None and c.jg_hidden_s >= 60 and c.gt >= 180, prio=3),
    T("jg_visible_far", "jungle", "{jg} est {jg_side} : c'est le moment de jouer agressif",
      lambda c: c.jg_visible and c.jg_side in ("top", "bot") and c.lane is not None
      and c.lane != c.jg_side, roles=LANERS, prio=3),
    T("jg_counter", "jungle", "{jg} est {jg_side} : contre-gank ou prends ses camps de l'autre côté",
      lambda c: c.jg_visible and c.jg_side in ("top", "bot"), roles=("JUNGLE",), prio=3),
    T("jg_first_clear", "jungle", "Avant 3:30, regarde où {jg} a commencé : il finit souvent de l'autre côté",
      lambda c: 90 <= c.gt <= 210, prio=2),
    T("jg_level3", "jungle", "Vers 3:15 le jungler ennemi est niveau 3 : premier gank possible",
      lambda c: 150 <= c.gt <= 240, roles=LANERS, prio=3),
    T("jg_missing_3", "map", "{missing} ennemis disparus : ne t'avance pas sans vision",
      lambda c: c.missing >= 3, prio=4),
    T("jg_track_camps", "jungle", "Un camp ennemi vide = ton jungler sait où il est passé : communique",
      lambda c: c.gt >= 200, prio=1),
    # ------------------------------------------------------------------ lane matchup
    T("lvl_ahead", "matchup", "Tu as {lvl} niveau(x) d'avance sur {opp} : force les échanges",
      lambda c: c.level_diff >= 1 and c.early, roles=LANERS, prio=3),
    T("lvl_behind", "matchup", "{opp} a {lvl} niveau(x) d'avance : évite les échanges, farme sous ta tour",
      lambda c: c.level_diff <= -1, roles=LANERS, prio=3),
    T("gold_ahead", "matchup", "Avance d'or sur {opp} : transforme-la en plaques et en tour",
      lambda c: c.gold_diff >= 800 and c.early, roles=LANERS, prio=3),
    T("gold_behind", "matchup", "Retard sur {opp} : joue pour le farm, pas pour le kill",
      lambda c: c.gold_diff <= -800, roles=LANERS, prio=3),
    T("cs_behind_opp", "matchup", "{opp} a plus de CS que toi : concentre-toi sur les derniers coups",
      lambda c: c.cs_diff <= -15, roles=CARRIES, prio=3),
    T("opp_dead", "matchup", "{opp} est mort : pousse ta vague et prends des plaques",
      lambda c: c.opp_dead and c.early, roles=LANERS, prio=4),
    T("opp_dead_late", "matchup", "{opp} est mort : pousse et prends la tour, puis rejoins ton équipe",
      lambda c: c.opp_dead and not c.early, roles=LANERS, prio=4),
    T("level2", "matchup", "Le premier à 2 niveaux gagne l'échange : tape la vague pour y arriver avant lui",
      lambda c: c.gt <= 120, roles=LANERS, prio=3),
    T("level6", "matchup", "Niveau 6 : cherche un échange avec ton ultime avant {opp}",
      lambda c: c.level == 6 and c.early, roles=LANERS, prio=3),
    T("fed_enemy", "matchup", "{fed} est très avancé : ne l'affronte pas seul",
      lambda c: bool(c.fed), prio=3),
    T("plates", "matchup", "Avant 14:00, chaque plaque rapporte de l'or : frappe la tour quand tu peux",
      lambda c: 300 <= c.gt < 840, roles=LANERS, prio=2),
    T("plates_end", "matchup", "Les plaques tombent à 14:00 : dernière chance de les prendre",
      lambda c: 720 <= c.gt < 840, roles=LANERS, prio=3),
    # ------------------------------------------------------------------ farm
    T("cs_low", "farm", "{cspm} CS/min, objectif {target} : reste sur la vague, ne rate pas les canons",
      lambda c: c.gt >= 300 and c.cs_target > 0 and c.cspm < c.cs_target - 1.0, roles=CARRIES, prio=3),
    T("cs_good", "farm", "{cspm} CS/min : bon farm, garde ce rythme",
      lambda c: c.gt >= 300 and c.cs_target > 0 and c.cspm >= c.cs_target, roles=CARRIES, prio=2),
    T("cs_cannon", "farm", "Le sbire canon vaut le plus d'or : ne le rate jamais", roles=CARRIES),
    T("cs_side", "farm", "Entre deux objectifs, va farmer une voie de côté au lieu d'errer au milieu",
      lambda c: c.mid or c.late, roles=CARRIES, prio=2),
    T("cs_jungle_mid", "farm", "Pas d'objectif dans 60 s ? Prends les camps de ta jungle proches de toi",
      lambda c: (c.mid or c.late) and not any(r <= 60 for r in c.soon.values()), roles=("MIDDLE", "BOTTOM"),
      prio=2),
    T("jg_cs", "farm", "{cspm} camps/min, vise {target} : enchaîne tes camps entre deux ganks",
      lambda c: c.gt >= 300 and c.cspm < c.cs_target - 0.8, roles=("JUNGLE",), prio=3),
    T("jg_full_clear", "farm", "Un full clear propre vaut mieux qu'un gank raté sur une voie poussée",
      lambda c: c.early, roles=("JUNGLE",), prio=2),
    T("cs_under_tower", "farm", "Sous ta tour : 2 coups de tour + 1 coup pour les sbires de mêlée",
      lambda c: c.wave == "pushed_in", roles=CARRIES, prio=3),
    # ------------------------------------------------------------------ waves
    T("wave_push_back", "wave", "Ta vague pousse : rentre en base après l'avoir poussée sous leur tour",
      lambda c: c.wave == "pushing" and c.gold >= 900, roles=LANERS, prio=3),
    T("wave_push_ward", "wave", "Ta vague pousse : tu es exposé aux ganks, garde une balise dans la rivière",
      lambda c: c.wave == "pushing" and not c.jg_visible, roles=LANERS, prio=3),
    T("wave_pushed_in", "wave", "La vague revient vers toi : laisse-la venir sous ta tour",
      lambda c: c.wave == "pushed_in", roles=LANERS, prio=3),
    T("wave_freeze", "wave", "En avance ? Bloque la vague près de ta tour pour priver {opp} de farm",
      lambda c: c.gold_diff >= 500 and c.early, roles=("TOP", "BOTTOM"), prio=2),
    T("wave_slow_push", "wave", "Pour un gank ou un retour, empile une grosse vague avant de partir",
      lambda c: c.early, roles=LANERS, prio=1),
    T("wave_crash_recall", "wave", "Rentre juste après avoir fait s'écraser ta vague sous leur tour",
      roles=LANERS),
    T("wave_side_late", "wave", "En fin de partie, ne va pas seul sur une voie de côté sans vision",
      lambda c: c.late, prio=2),
    # ------------------------------------------------------------------ vision
    T("vis_river_3", "vision", "Pose une balise dans la rivière avant 3:00",
      lambda c: 60 <= c.gt <= 180, roles=LANERS, prio=3),
    T("vis_control", "vision", "Pas de balise de contrôle : achète-en une au prochain retour",
      lambda c: not c.has_control_ward and c.gt >= 300, prio=2),
    T("vis_sweeper", "vision", "Passe au Balayeur : la vision ennemie autour des objectifs fait la différence",
      lambda c: c.gt >= 600 and SWEEPER not in c.items, roles=("UTILITY", "JUNGLE"), prio=2),
    T("vis_score_low", "vision", "Score de vision {ward} : utilise ta balise dès qu'elle est rechargée",
      lambda c: c.gt >= 600 and c.ward_score < c.minute * 0.6, prio=3),
    T("vis_trinket", "vision", "Ta balise jaune se recharge : ne la garde pas dans ton inventaire", prio=1),
    T("vis_deep", "vision", "Une balise dans la jungle ennemie montre où va leur jungler",
      lambda c: c.gt >= 300, roles=("UTILITY", "JUNGLE", "MIDDLE"), prio=2),
    T("vis_tribush", "vision", "Top : une balise au tribrush protège des ganks par la rivière",
      lambda c: c.early, roles=("TOP",), prio=2),
    T("vis_mid_sides", "vision", "Mid : balise un côté de la rivière, joue près de l'autre",
      lambda c: c.early, roles=("MIDDLE",), prio=2),
    T("vis_bot_bush", "vision", "Bot : contrôle le buisson de la voie pour éviter les engages",
      lambda c: c.early, roles=("BOTTOM", "UTILITY"), prio=2),
    T("vis_sup_objective", "vision", "Support : place la vision 60 s avant chaque objectif",
      lambda c: any(30 <= r <= 90 for r in c.soon.values()), roles=("UTILITY",), prio=3),
    T("vis_clear", "vision", "Casse les balises ennemies que tu vois : chaque balise cassée rapporte de l'or",
      prio=1),
    T("vis_minimap", "vision", "Regarde ta minimap toutes les 5 secondes", prio=1, cooldown=900.0),
    # ------------------------------------------------------------------ items / gold
    T("gold_back", "items", "{gold} pièces d'or : un retour en base maintenant te donne un objet",
      lambda c: c.gold >= 1300 and not c.in_base, prio=3),
    T("gold_spend", "items", "Ne garde pas ton or : dépense-le à chaque retour", lambda c: c.in_base and c.gold >= 500,
      prio=2),
    T("boots", "items", "Pas encore de bottes : la vitesse évite beaucoup de ganks",
      lambda c: c.gt >= 600 and not c.has_boots, roles=CARRIES, prio=3),
    T("first_item", "items", "Un objet terminé vaut mieux que trois composants : finis ton premier objet",
      lambda c: 600 <= c.gt <= 1200, roles=CARRIES, prio=1),
    T("anti_heal", "items", "Beaucoup de soins en face ? Pense à un objet anti-soins", lambda c: c.gt >= 900, prio=1),
    T("defensive", "items", "Tu meurs souvent : un objet défensif te gardera en vie",
      lambda c: c.recent_deaths >= 2 and c.gt >= 600, prio=3),
    T("sup_quest", "items", "Termine ta quête de support pour débloquer les balises",
      lambda c: c.gt <= 600, roles=("UTILITY",), prio=2),
    T("elixir", "items", "Fin de partie : un élixir avant le combat de Baron fait la différence",
      lambda c: c.late and c.gold >= 500, prio=2),
    T("potions", "items", "Début de partie : une potion supplémentaire évite un retour forcé",
      lambda c: c.gt <= 300, roles=LANERS, prio=1),
    # ------------------------------------------------------------------ survival / deaths
    T("deaths_many", "survival", "{deaths} morts : joue plus près de ta tour tant que tu es en retard",
      lambda c: c.recent_deaths >= 2, prio=4),
    T("low_hp", "survival", "Peu de PV : rentre plutôt que de rester et donner un kill",
      lambda c: c.hp is not None and c.hp < 0.3 and not c.in_base, prio=4),
    T("stance_safe", "survival", "Situation défavorable : joue la vague et attends ton équipe",
      lambda c: c.stance == "prudent", prio=3),
    T("stance_aggro", "survival", "Situation favorable : mets la pression, mais garde une sortie",
      lambda c: c.stance == "agressif", prio=3),
    T("flash_respect", "survival", "Sans Saut éclair, recule d'un pas : tu es plus facile à attraper", prio=1),
    T("death_timer", "survival", "En fin de partie, une mort = 40 s d'absence : ne prends pas de risque seul",
      lambda c: c.late, prio=2),
    T("dead_watch", "survival", "Mort : regarde la carte, où sont les ennemis et quel objectif suivre",
      lambda c: c.dead, prio=4, cooldown=240.0),
    T("tower_dive", "survival", "Ne plonge sous une tour que si les sbires prennent les coups", prio=1),
    T("chase", "survival", "Ne chasse pas trop loin : un kill ne vaut pas une mort", prio=1),
    # ------------------------------------------------------------------ teamfight / macro
    T("group_mid", "macro", "Milieu de partie : regroupez-vous pour les objectifs, pas pour errer",
      lambda c: c.mid, prio=2),
    T("teamfight_adc", "macro", "En combat, tape la cible la plus proche sans t'avancer",
      lambda c: not c.early, roles=("BOTTOM",), prio=2),
    T("teamfight_sup", "macro", "En combat, reste près de ton ADC et protège-le", lambda c: not c.early,
      roles=("UTILITY",), prio=2),
    T("teamfight_top", "macro", "Top : téléporte-toi sur les combats d'objectif si ta voie est poussée",
      lambda c: c.gt >= 600, roles=("TOP",), prio=2),
    T("mid_roam", "macro", "Mid : après avoir poussé ta vague, va aider top ou bot",
      lambda c: 240 <= c.gt <= 1200, roles=("MIDDLE",), prio=2),
    T("jg_gank_lanes", "macro", "Gank les voies où l'ennemi a poussé et n'a pas de balise", roles=("JUNGLE",),
      prio=2),
    T("jg_scuttle", "macro", "Prends le Carapateur avec l'aide de ta voie qui a la priorité",
      lambda c: 180 <= c.gt <= 420, roles=("JUNGLE",), prio=3),
    T("sup_roam", "macro", "Support : quand ton ADC rentre, va poser de la vision mid ou top",
      lambda c: 300 <= c.gt <= 1200, roles=("UTILITY",), prio=2),
    T("ahead_team", "macro", "Équipe en avance : jouez groupés et prenez les tours une par une",
      lambda c: c.team_gold_diff >= 3000, prio=3),
    T("behind_team", "macro", "Équipe en retard : défendez sous les tours et attendez une erreur",
      lambda c: c.team_gold_diff <= -3000, prio=3),
    T("inhib_respawn", "macro", "Un inhibiteur repousse en 5 min : profitez-en pour prendre Baron ou dragon",
      lambda c: c.late, prio=1),
    T("split", "macro", "Si tu pousses seul, regarde la minimap : recule quand 3 ennemis disparaissent",
      lambda c: c.mid or c.late, roles=("TOP", "MIDDLE"), prio=2),
    T("ping", "macro", "Signale (ping) les disparitions de ton adversaire à ton équipe", roles=LANERS),
    T("recall_sync", "macro", "Rentre en même temps que ton équipe pour ne pas jouer en sous-nombre",
      lambda c: not c.early, prio=1),
    T("objective_first", "macro", "Les tours et objectifs gagnent les parties, pas les kills", prio=1),
    # ------------------------------------------------------------------ role basics (generic)
    T("top_tp", "role", "Top : garde ta Téléportation pour revenir en voie ou rejoindre un combat",
      lambda c: c.early, roles=("TOP",), prio=1),
    T("top_trade", "role", "Top : échange quand ses sorts sont utilisés sur les sbires", roles=("TOP",)),
    T("mid_prio", "role", "Mid : la priorité mid permet à ton jungler d'envahir", roles=("MIDDLE",)),
    T("mid_shove", "role", "Mid : pousse ta vague avant chaque objectif pour y arriver en premier",
      roles=("MIDDLE",), prio=2),
    T("adc_position", "role", "ADC : reste derrière tes sbires pour éviter les engages", roles=("BOTTOM",)),
    T("adc_last_hit", "role", "ADC : chaque dernier coup compte, ton or vient du farm", roles=("BOTTOM",)),
    T("sup_brush", "role", "Support : un buisson contrôlé limite les options de l'ennemi", roles=("UTILITY",)),
    T("sup_ward_timing", "role", "Support : balise avant que ton jungler arrive pour le gank", roles=("UTILITY",)),
    T("jg_path", "role", "Jungler : planifie ton chemin vers la voie qui peut gagner le combat",
      roles=("JUNGLE",)),
    T("jg_track_smite", "role", "Jungler : ton Châtiment sert aussi à sécuriser un kill bas en PV",
      roles=("JUNGLE",)),
    T("jg_invade", "role", "Jungler : envahis seulement si tes voies proches ont la priorité",
      roles=("JUNGLE",), prio=2),
    # ------------------------------------------------------------------ phase reminders
    T("early_safe", "phase", "Les 3 premières minutes : évite de mourir, c'est l'or le plus cher de la partie",
      lambda c: c.gt <= 180, prio=2),
    T("first_back", "phase", "Premier retour : vise environ 1100 pièces d'or pour un vrai composant",
      lambda c: 240 <= c.gt <= 480, roles=LANERS, prio=2),
    T("mid_transition", "phase", "Après 14:00 les plaques ont disparu : jouez ensemble pour les objectifs",
      lambda c: 840 <= c.gt <= 960, prio=3),
    T("late_vision", "phase", "Fin de partie : avance avec la vision, jamais dans le noir",
      lambda c: c.late, prio=2),
    T("late_baron_spawn", "phase", "Fin de partie : le Baron décide souvent la partie, gardez-le en tête",
      lambda c: c.gt >= 1200, prio=1),
    T("calm", "mental", "Respire : une partie se joue sur 30 minutes, pas sur une erreur", prio=1, cooldown=1200.0),
    T("mute", "mental", "Coupe le chat si quelqu'un est négatif : garde ta concentration", prio=1,
      cooldown=1500.0),
    T("focus_one", "mental", "Concentre-toi sur une amélioration par partie (farm, vision ou morts)", prio=1,
      cooldown=1500.0),
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


def build_context(facts: dict[str, Any] | None, game: Any, scoreboard: Any = None, stance: Any = None) -> TipContext:
    """:class:`TipContext` from the coach facts + Live Client data + Tab summary. Never raises."""
    c = TipContext()
    try:
        f = facts or {}
        me = getattr(game, "me", None)
        c.gt = _f(f.get("gt"), None) or _f(getattr(game, "game_time", None), 0.0) or 0.0
        role = f.get("my_role") or str(getattr(me, "position", "") or "").upper() or None
        c.role = role if role in ROLES else None
        c.lane = f.get("role_lane")
        c.dead = bool(getattr(me, "is_dead", False))
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


class TipRotator:
    """One written tip at a time, rotating every :data:`ROTATE_S` (see the module docstring)."""

    def __init__(self, seed: int | None = None, rotate_s: float = ROTATE_S, tips: tuple[Tip, ...] = TIPS) -> None:
        self._lock = threading.Lock()
        self._rng = random.Random(seed)
        self.rotate_s = float(rotate_s)
        self._tips = tuple(tips)
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._current: Tip | None = None
            self._text: str | None = None
            self._since: float | None = None
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

    def history(self) -> list[str]:
        with self._lock:
            return list(self._history)

    def update(self, t: float, ctx: TipContext) -> str | None:
        """Current tip text (changes every ``rotate_s``). Never raises."""
        try:
            with self._lock:
                return self._update_locked(float(t), ctx)
        except Exception:
            log.exception("TipRotator.update failed")
            return None

    def _update_locked(self, t: float, ctx: TipContext) -> str | None:
        gt = float(ctx.gt)
        if self._last_gt is not None and gt < self._last_gt - 5.0:     # new game / loop
            self._shown_gt.clear()
            self._current = None
            self._since = None
        self._last_gt = gt
        cur = self._current
        if cur is not None and self._since is not None:
            age = t - self._since
            still = cur.applies(ctx)
            if age < 0:
                self._since = t
            elif (age < self.rotate_s and still) or age < MIN_SHOW_S:
                self._text = cur.render(ctx)         # numbers in the text stay fresh
                return self._text
        tip = self._pick(ctx, gt)
        if tip is None:
            if cur is not None and not cur.applies(ctx):
                self._current, self._text = None, None
            return self._text
        self._current, self._since = tip, t
        self._last_cat = tip.category
        self._shown_gt[tip.id] = gt
        self._text = tip.render(ctx)
        self._history.append(tip.id)
        del self._history[:-200]
        return self._text

    def _pick(self, ctx: TipContext, gt: float) -> Tip | None:
        cands = []
        for tip in self._tips:
            if self._current is not None and tip.id == self._current.id:
                continue
            last = self._shown_gt.get(tip.id)
            if last is not None and 0.0 <= gt - last < tip.cooldown:
                continue
            if tip.applies(ctx):
                cands.append(tip)
        if not cands:
            return None
        other = [tp for tp in cands if tp.category != self._last_cat]
        pool = other or cands
        best = max(tp.prio for tp in pool)
        # mostly the most relevant tier, sometimes one tier below (variety)
        tier = [tp for tp in pool if tp.prio >= best - (1 if self._rng.random() < 0.25 else 0)]
        weights = [float(tp.prio) ** 2 for tp in tier]
        return self._rng.choices(tier, weights=weights, k=1)[0]


__all__ = ["Tip", "TipContext", "TipRotator", "TIPS", "build_context", "tip_count", "ROTATE_S"]
