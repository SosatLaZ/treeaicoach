"""Praise: notice when the player does something genuinely good, and say it (briefly).

:class:`PraiseCoach` watches the official Live Client data (event feed, my scores, items,
gold and health), the gank alerts raised by the engine and the Tab scoreboard summary
(:mod:`treeaicoach.scoreboard`) and produces, rarely, ONE short French compliment with toast
texts (:class:`Praise`):

* kills: first blood, double / triple / quadra / penta kill, shutdown (victim on a 3+ kill
  streak in the event feed), solo kill (no assister) - "Joli solo kill sur Darius !";
* epic objectives / structures taken with my participation (killer or assister), steals;
* a DANGER gank survived (no death within 10 s of the last DANGER alert): "Bien esquivé ce gank !";
* an escape at low health (``championStats.currentHealth`` dropped under 20 % during a fight,
  still alive 10 s later);
* CS checkpoints at 15:00 and 25:00 (the 10:00 one is the macro coach's): "8,3 CS par minute, propre !";
* vision score milestones, lead over my lane opponent growing (Tab item gold), a clean back
  (big purchase, almost no gold left) and the completion of a major item.

Policy: at most one compliment every :data:`MIN_GAP_S` (45 s; big plays such as a multikill
only need :data:`BIG_GAP_S`), never while a gank threat is active nor during
:data:`QUIET_AFTER_THREAT_S` after it (a kill made during a fight is praised once it is
over, if still fresh). Sentences are picked at random (seeded ``random.Random``) among several
variants, never the same twice in a row for one kind.

Pure Python, thread-safe, never raises from its public methods.
"""

from __future__ import annotations

import logging
import math
import random
import threading
from dataclasses import dataclass
from typing import Any

from treeaicoach.fmtutil import finite_loose
from treeaicoach.scoreboard import fmt_dec, fmt_gold, item_info, items_gold, major_items, player_names

log = logging.getLogger(__name__)

MIN_GAP_S = 45.0
BIG_GAP_S = 12.0              # priority >= BIG_PRIORITY only needs this since the last praise
BIG_PRIORITY = 75
QUIET_AFTER_THREAT_S = 4.0
GANK_SURVIVE_S = 10.0
GANK_EPISODE_S = 20.0         # DANGER alerts closer than this belong to the same gank
LOW_HP_FRAC = 0.2
FIGHT_DROP_FROM = 0.5         # "fight": health went from >= 50 % to < 20 % ...
FIGHT_DROP_WINDOW = 15.0      # ... within this many seconds
ESCAPE_SURVIVE_S = 10.0
CS_CHECKPOINTS = (900.0, 1500.0)
CS_WINDOW_S = 90.0
CS_TARGET = {"JUNGLE": 6.0}
CS_DEFAULT_TARGET = 8.0
VISION_STEPS = (15, 30, 50, 75)
VISION_STEPS_SUPPORT = (30, 60, 90, 120)
LEAD_TIERS = (1000, 2500, 4000)
BACK_MIN_SPENT = 900
BACK_MAX_LEFT = 350

TTL_S: dict[str, float] = {
    "multikill": 30.0, "first_blood": 30.0, "shutdown": 30.0, "solo_kill": 30.0, "kill": 20.0,
    "objective": 40.0, "steal": 40.0, "gank_dodge": 20.0, "escape": 20.0, "cs": 90.0,
    "vision": 90.0, "lead": 90.0, "back": 60.0, "item": 60.0,
}
PRIORITY: dict[str, int] = {
    "steal": 95, "multikill": 90, "first_blood": 80, "shutdown": 78, "solo_kill": 76,
    "escape": 70, "gank_dodge": 65, "objective": 55, "lead": 50, "item": 45, "kill": 40, "cs": 35,
    "back": 32, "vision": 30,
}
TOAST_TITLE: dict[str, str] = {
    "steal": "VOL !", "first_blood": "PREMIER SANG", "shutdown": "SHUTDOWN", "solo_kill": "SOLO KILL",
    "escape": "BELLE FUITE", "gank_dodge": "GANK ESQUIVÉ", "objective": "OBJECTIF", "lead": "LANE DOMINÉE",
    "item": "PIC DE PUISSANCE", "kill": "KILL", "cs": "FARM", "back": "RETOUR PROPRE",
    "vision": "VISION",
}
MULTI_TITLE = {2: "DOUBLÉ", 3: "TRIPLÉ", 4: "QUADRUPLÉ", 5: "PENTAKILL"}
MULTI_WORD = {2: "Double kill", 3: "Triple kill", 4: "Quadra kill", 5: "PENTAKILL"}

PHRASES: dict[str, tuple[str, ...]] = {
    "solo_kill_lane": ("Joli solo kill sur {name} !", "{name} en solo, propre !",
                       "Solo kill sur {name}, tu domines !"),
    "solo_kill": ("Joli solo kill sur {name} !", "{name} tout seul, bien joué !", "Propre, le solo sur {name} !"),
    "kill": ("Bien joué pour {name} !", "{name} tombe, bien joué.", "Joli kill sur {name}."),
    "shutdown": ("Shutdown sur {name}, énorme !", "Tu as arrêté {name}, bien joué !",
                 "Grosse prime récupérée sur {name} !"),
    "first_blood": ("Premier sang, bien joué !", "First blood pour toi, propre !",
                    "Premier sang, tu lances bien la partie !"),
    "multikill": ("{word}, magnifique !", "{word} ! Quel combat !", "{word}, superbe !"),
    "objective": ("{obj} pris, bien joué !", "Bien joué pour {obj_le} !", "{obj} sécurisé, propre !"),
    "steal": ("Vol {obj_du}, incroyable !", "Tu as volé {obj_le}, énorme !"),
    "structure": ("{obj} détruite, bien joué !", "Bien joué pour {obj_la} !"),
    "gank_dodge": ("Bien esquivé ce gank !", "Gank évité, bien vu !", "Bien réagi, tu as évité le gank."),
    "escape": ("Belle fuite à bas PV !", "Bien survécu, c'était chaud !", "Sorti de là à bas PV, bien joué !"),
    "cs": ("{cspm} sbires par minute, propre !", "Super : {cspm} sbires par minute.",
           "{cspm} sbires par minute, continue comme ça !"),
    "vision": ("Score de vision à {n}, excellent !", "Vision au top : {n} de score.",
               "{n} de score de vision, bien joué !"),
    "lead": ("Tu domines ta voie : {gold} sur {name}.", "{gold} d'avance sur {name}, continue !",
             "Belle avance sur {name} : {gold}."),
    "back": ("Retour propre, tout ton or est dépensé.", "Bon retour en base, rien de gaspillé.",
             "Achat efficace, bien joué."),
    "item": ("{item} terminé, pic de puissance !", "{item} en poche, à toi de jouer !",
             "{item} terminé, tu es plus fort maintenant."),
}

OBJECTIVES: dict[str, tuple[str, str, str, str]] = {
    # EventName -> (name, "le ...", "du ...", gender)  (gender "m" | "f" used for structures)
    "DragonKill": ("Dragon", "le dragon", "du dragon", "m"),
    "HeraldKill": ("Héraut", "le Héraut", "du Héraut", "m"),
    "BaronKill": ("Baron", "le Baron", "du Baron", "m"),
    "HordeKill": ("Larves", "les larves", "des larves", "f"),
}
STRUCTURES: dict[str, tuple[str, str]] = {
    "TurretKilled": ("Tour", "la tour"),
    "InhibKilled": ("Inhibiteur", "l'inhibiteur"),
}


@dataclass(frozen=True)
class Praise:
    kind: str
    key: str
    text: str                  # French voice sentence
    title: str                 # toast title
    subtitle: str              # toast subtitle
    t: float
    priority: int = 0
    alias: str | None = None   # champion alias for the toast icon (victim, or me)


def _finite(x: Any, default: float = 0.0) -> float:
    return finite_loose(x, default)  # type: ignore[return-value]


def _truthy(x: Any) -> bool:
    if isinstance(x, str):
        return x.strip().lower() in ("true", "1", "yes")
    return bool(x)


class PraiseCoach:
    """See the module docstring."""

    def __init__(self, seed: int | None = None, min_gap_s: float = MIN_GAP_S) -> None:
        self._lock = threading.RLock()
        self._rng = random.Random(seed)
        self.min_gap_s = float(min_gap_s)
        self.reset()

    # ------------------------------------------------------------------ public
    def reset(self) -> None:
        with self._lock:
            self._seen_events: set[Any] = set()
            self._events_ready = False
            self._pending: list[Praise] = []
            self._last_emit = -math.inf
            self._last_threat = -math.inf
            self._last_phrase: dict[str, str] = {}
            self._danger_first: float | None = None
            self._danger_last: float | None = None
            self._died_since_danger = False
            self._hp_hist: list[tuple[float, float]] = []
            self._low_since: float | None = None
            self._was_dead = False
            self._cs_done: set[float] = set()
            self._vision_done: float | None = None
            self._lead_tier = 0
            self._last_lead: int | None = None
            self._items_gold: int | None = None
            self._majors: list[int] | None = None
            self._last_fetch: Any = None
            self._history: list[Praise] = []

    def note_danger(self, t: float) -> None:
        """The engine raised a DANGER gank alert at ``t``."""
        with self._lock:
            t = float(t)
            if self._danger_last is None or t - self._danger_last > GANK_EPISODE_S:
                self._danger_first = t
                self._died_since_danger = False
            self._danger_last = t

    def history(self) -> list[Praise]:
        with self._lock:
            return list(self._history)

    def update(self, t: float, game: Any, threat: int = 0, scoreboard: Any = None,
               role: str | None = None) -> list[Praise]:
        """One tick; returns at most one praise to say/show now. Never raises."""
        try:
            with self._lock:
                return self._update_locked(float(t), game, int(threat or 0), scoreboard, role)
        except Exception:
            log.exception("PraiseCoach.update failed")
            return []

    # ------------------------------------------------------------------ internals
    def _phrase(self, group: str, **kw: Any) -> str:
        options = PHRASES.get(group) or ("Bien joué !",)
        last = self._last_phrase.get(group)
        choices = [p for p in options if p != last] or list(options)
        tpl = self._rng.choice(choices)
        self._last_phrase[group] = tpl
        try:
            return tpl.format(**kw)
        except (KeyError, IndexError, ValueError):
            return "Bien joué !"

    def _add(self, kind: str, key: str, text: str, subtitle: str, t: float, alias: str | None = None,
             title: str | None = None, priority: int | None = None) -> None:
        if any(p.key == key for p in self._pending):
            return
        self._pending.append(Praise(kind, key, text, title or TOAST_TITLE.get(kind, "BIEN JOUÉ"),
                                    subtitle, t, PRIORITY.get(kind, 10) if priority is None else priority,
                                    alias))

    def _update_locked(self, t: float, game: Any, threat: int, scoreboard: Any, role: str | None) -> list[Praise]:
        if threat > 0:
            self._last_threat = t
        me = getattr(game, "me", None) if game is not None else None
        if me is None:
            return []
        dead = bool(me.is_dead)
        if dead and not self._was_dead:
            self._died_since_danger = True
            self._low_since = None
            # a death cancels pending survival praise
            self._pending = [p for p in self._pending if p.kind not in ("gank_dodge", "escape")]
        self._was_dead = dead
        gt = max(0.0, _finite(getattr(game, "game_time", 0.0)))
        fetched = getattr(game, "fetched_at", None)
        if fetched is None or fetched != self._last_fetch:
            self._last_fetch = fetched
            self._on_poll(t, gt, game, me, scoreboard, role)
        self._survival(t, dead)
        return self._emit(t, threat)

    # -- per poll ----------------------------------------------------------------------
    def _on_poll(self, t: float, gt: float, game: Any, me: Any, scoreboard: Any, role: str | None) -> None:
        self._events(t, game, me, scoreboard)
        self._health(t, game, me)
        role = role or (getattr(me, "position", "") or None)
        self._cs(t, gt, me, role)
        self._vision(t, me, role)
        self._lead(t, scoreboard)
        self._shopping(t, game, me)

    def _events(self, t: float, game: Any, me: Any, scoreboard: Any) -> None:
        events = [e for e in (getattr(game, "events", None) or []) if isinstance(e, dict)]
        mine = player_names(me)
        by_name: dict[str, Any] = {}
        for p in game.all_players():
            for n in player_names(p):
                by_name.setdefault(n, p)
        new: list[dict] = []
        for e in events:
            k = e.get("EventID", (e.get("EventName"), e.get("EventTime")))
            if k in self._seen_events:
                continue
            self._seen_events.add(k)
            new.append(e)
        if not self._events_ready:           # joined mid-game: never praise the past
            self._events_ready = True
            return
        lane_opps: set[str] = set()
        m = getattr(scoreboard, "my_matchup", None)
        if m is not None:
            lane_opps.add(m.enemy_alias)

        def is_me(name: Any) -> bool:
            n = str(name or "").strip().casefold()
            return bool(n) and (n in mine or n.split("#", 1)[0] in mine)

        def who(name: Any) -> Any:
            n = str(name or "").strip().casefold()
            return by_name.get(n) or by_name.get(n.split("#", 1)[0])

        for e in new:
            name = e.get("EventName")
            assisters = e.get("Assisters") or []
            if not isinstance(assisters, list):
                assisters = []
            if name == "ChampionKill" and is_me(e.get("KillerName")):
                victim = who(e.get("VictimName"))
                vname = victim.champion_name if victim is not None else "ta cible"
                valias = victim.champion_alias if victim is not None else None
                streak = self._streak(events, e)
                if streak >= 3:
                    self._add("shutdown", f"shutdown:{e.get('EventID')}", self._phrase("shutdown", name=vname),
                              f"{vname} arrêté ({streak} kills d'affilée)", t, valias)
                elif not assisters and valias in lane_opps:
                    self._add("solo_kill", f"solo:{e.get('EventID')}", self._phrase("solo_kill_lane", name=vname),
                              f"Solo kill sur ton adversaire {vname}", t, valias)
                elif not assisters:
                    self._add("solo_kill", f"solo:{e.get('EventID')}", self._phrase("solo_kill", name=vname),
                              f"{vname} éliminé en solo", t, valias)
                else:
                    self._add("kill", f"kill:{e.get('EventID')}", self._phrase("kill", name=vname),
                              f"{vname} éliminé", t, valias)
            elif name == "Multikill" and is_me(e.get("KillerName")):
                n = int(min(5, max(2, _finite(e.get("KillStreak"), 2))))
                # a multikill supersedes the single kill praise(s) of the same fight
                self._pending = [p for p in self._pending if p.kind not in ("kill", "solo_kill")]
                self._add("multikill", f"multi:{e.get('EventID')}", self._phrase("multikill", word=MULTI_WORD[n]),
                          f"{MULTI_WORD[n]} !", t, me.champion_alias, title=MULTI_TITLE[n],
                          priority=PRIORITY["multikill"] + n)
            elif name == "FirstBlood" and is_me(e.get("Recipient")):
                self._add("first_blood", "first_blood", self._phrase("first_blood", name="ta cible"),
                          "Le premier kill de la partie", t, me.champion_alias)
            elif name in OBJECTIVES and (is_me(e.get("KillerName")) or any(is_me(a) for a in assisters)):
                obj, obj_le, obj_du, _g = OBJECTIVES[name]
                if name == "DragonKill":
                    dtype = str(e.get("DragonType") or "")
                    if dtype.lower() == "elder":
                        obj, obj_le, obj_du = "Dragon ancestral", "le dragon ancestral", "du dragon ancestral"
                plural_f = _g == "f"                       # "Larves" : feminine plural agreement
                if _truthy(e.get("Stolen")):
                    self._add("steal", f"steal:{e.get('EventID')}", self._phrase("steal", obj_le=obj_le, obj_du=obj_du),
                              f"{obj} {'volées' if plural_f else 'volé'} !", t, me.champion_alias)
                else:
                    text = self._phrase("objective", obj=obj, obj_le=obj_le)
                    if plural_f:
                        text = text.replace(" pris,", " prises,").replace(" sécurisé,", " sécurisées,")
                    self._add("objective", f"obj:{e.get('EventID')}", text,
                              f"{obj} pour ton équipe", t, me.champion_alias)
            elif name in STRUCTURES and (is_me(e.get("KillerName")) or any(is_me(a) for a in assisters)):
                obj, obj_la = STRUCTURES[name]
                text = self._phrase("structure", obj=obj, obj_la=obj_la)
                if name == "InhibKilled":
                    text = text.replace("détruite", "détruit")
                self._add("objective", f"obj:{e.get('EventID')}", text, f"{obj} détruit{'e' if obj == 'Tour' else ''}",
                          t, me.champion_alias, title="STRUCTURE")

    @staticmethod
    def _streak(events: list[dict], kill: dict) -> int:
        """Kills of the victim since their last death, before ``kill`` (event feed)."""
        victim = str(kill.get("VictimName") or "").strip().casefold()
        t_kill = _finite(kill.get("EventTime"))
        if not victim:
            return 0
        streak = 0
        for e in events:
            if e.get("EventName") != "ChampionKill" or e is kill:
                continue
            if _finite(e.get("EventTime")) > t_kill:
                continue
            if str(e.get("VictimName") or "").strip().casefold() == victim:
                streak = 0
            elif str(e.get("KillerName") or "").strip().casefold() == victim:
                streak += 1
        return streak

    def _health(self, t: float, game: Any, me: Any) -> None:
        stats = getattr(game, "champion_stats", None) or {}
        cur, mx = _finite(stats.get("currentHealth"), -1.0), _finite(stats.get("maxHealth"), 0.0)
        if cur < 0 or mx <= 0 or me.is_dead:
            return
        frac = cur / mx
        self._hp_hist = [(tt, f) for tt, f in self._hp_hist if t - tt <= FIGHT_DROP_WINDOW] + [(t, frac)]
        if frac < LOW_HP_FRAC and self._low_since is None:
            fight = any(f >= FIGHT_DROP_FROM for _tt, f in self._hp_hist)
            recent_threat = t - self._last_threat <= FIGHT_DROP_WINDOW
            if fight or recent_threat:
                self._low_since = t

    def _survival(self, t: float, dead: bool) -> None:
        if self._low_since is not None and not dead and t - self._low_since >= ESCAPE_SURVIVE_S:
            self._low_since = None
            self._add("escape", f"escape:{int(t)}", self._phrase("escape"), "Survécu à moins de 20 % de PV", t)
            if self._danger_last is not None and t - self._danger_last < GANK_SURVIVE_S + 5:
                self._danger_first = self._danger_last = None    # one praise for the same play
        if self._danger_last is not None and t - self._danger_last >= GANK_SURVIVE_S:
            if not self._died_since_danger and not dead:
                self._add("gank_dodge", f"gank:{int(self._danger_first or t)}", self._phrase("gank_dodge"),
                          "Danger évité sans mourir", t)
            self._danger_first = self._danger_last = None
            self._died_since_danger = False

    def _cs(self, t: float, gt: float, me: Any, role: str | None) -> None:
        if role == "UTILITY":
            return
        target = CS_TARGET.get(role or "", CS_DEFAULT_TARGET)
        for cp in CS_CHECKPOINTS:
            if cp in self._cs_done or not (cp <= gt <= cp + CS_WINDOW_S):
                continue
            self._cs_done.add(cp)
            cspm = me.creep_score / (gt / 60.0)
            if cspm >= target:
                txt = fmt_dec(round(cspm, 1))
                self._add("cs", f"cs:{int(cp)}", self._phrase("cs", cspm=txt),
                          f"{txt} sbires/min à {int(cp // 60)} min", t)

    def _vision(self, t: float, me: Any, role: str | None) -> None:
        steps = VISION_STEPS_SUPPORT if role == "UTILITY" else VISION_STEPS
        ws = float(me.ward_score)
        reached = [s for s in steps if ws >= s]
        if not reached:
            if self._vision_done is None:
                self._vision_done = 0.0
            return
        top = reached[-1]
        if self._vision_done is None:      # first poll (maybe joined mid-game): baseline only
            self._vision_done = top
            return
        if top > self._vision_done:
            self._vision_done = top
            self._add("vision", f"vision:{top}", self._phrase("vision", n=int(top)),
                      f"Score de vision : {int(ws)}", t)

    def _lead(self, t: float, scoreboard: Any) -> None:
        m = getattr(scoreboard, "my_matchup", None)
        if m is None:
            return
        lead = int(m.gold_diff)
        prev = self._last_lead
        self._last_lead = lead
        tier = sum(1 for x in LEAD_TIERS if lead >= x)
        if prev is None:
            self._lead_tier = tier        # baseline (joined mid-game)
            return
        if tier > self._lead_tier and lead > prev and m.cs_diff >= 0:
            self._lead_tier = tier
            gold = fmt_gold(lead)
            self._add("lead", f"lead:{tier}", self._phrase("lead", gold=gold, name=m.enemy),
                      f"{gold} et {m.cs_diff:+d} sbires sur {m.enemy}", t, m.enemy_alias)

    def _shopping(self, t: float, game: Any, me: Any) -> None:
        ig = items_gold(me.items)
        majors = major_items(me.items)
        prev_ig, prev_majors = self._items_gold, self._majors
        self._items_gold, self._majors = ig, majors
        if prev_ig is None or prev_majors is None:
            return
        new = [i for i in majors if i not in prev_majors]
        if new:
            info = item_info(new[-1])
            name = info[0] if info else "Objet"
            self._add("item", f"item:{new[-1]}:{len(majors)}", self._phrase("item", item=name),
                      f"{name} terminé", t, me.champion_alias)
        elif ig - prev_ig >= BACK_MIN_SPENT and _finite(getattr(game, "current_gold", 0.0)) <= BACK_MAX_LEFT:
            self._add("back", f"back:{int(t)}", self._phrase("back"),
                      f"{fmt_gold(ig - prev_ig, signed=False)} investis", t, me.champion_alias)

    # -- output ------------------------------------------------------------------------
    def _emit(self, t: float, threat: int) -> list[Praise]:
        self._pending = [p for p in self._pending if 0.0 <= t - p.t <= TTL_S.get(p.kind, 30.0)]
        if not self._pending or threat > 0 or t - self._last_threat < QUIET_AFTER_THREAT_S:
            return []
        best = max(self._pending, key=lambda p: (p.priority, p.t))
        gap = BIG_GAP_S if best.priority >= BIG_PRIORITY else self.min_gap_s
        if t - self._last_emit < gap:
            return []
        self._pending.remove(best)
        # lower-priority praises of the same moment are dropped: one compliment per play
        self._pending = [p for p in self._pending if p.t > best.t + 1.0 or p.kind in ("cs", "vision", "lead")]
        self._last_emit = t
        self._history.append(best)
        del self._history[:-50]
        return [best]
