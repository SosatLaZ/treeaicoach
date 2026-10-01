"""Game phase awareness + end-game calls (Live Client events, game time, death timers).

:func:`map_state` reads the public Live Client data of one poll (events: ``TurretKilled``,
``InhibKilled`` / ``InhibRespawned``, ``DragonKill``, ``BaronKill``, ``Ace``, ``ChampionKill``;
the players' ``isDead`` / ``respawnTimer``) and returns an immutable :class:`MapState`:

* the **phase**: ``"laning"`` (0 - ~14 min, until outer towers start falling: 14:00, or 2 outer
  towers down, or one of MY lane's outer towers), ``"mid"`` (rotations), ``"late"`` (>= 25 min
  or after the first Baron), ``"end"`` (an inhibitor down, Elder dragon taken / soul taken past
  30 min, or death timers >= 40 s);
* the standing structures (turret uv positions per team), inhibitors down, dragons per team
  (soul point), Baron / Elder buff owner and remaining time, dead enemies with their respawn
  timers, a recent ace.

:class:`EndGameCaller` turns it into rare, important calls (ace -> Baron / finish, enemy carries
dead -> push, Elder soon -> don't get caught, enemy Baron -> defend, soul point, inhibitors):
:class:`MacroCall` with a French text, a short banner title, a minimap target (arrow) and
whether it is worth speaking. Pure Python, thread-safe, never raises from its public API.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from dataclasses import dataclass, field
from typing import Any

from treeaicoach import geometry

log = logging.getLogger(__name__)

LANING_END_GT = 840.0          # 14:00: plates fall, laning phase over
LATE_GT = 1500.0               # 25:00
END_DEATH_TIMER_S = 40.0       # death timers this long = the game can end on one fight
BARON_BUFF_S = 180.0
ELDER_BUFF_S = 150.0
INHIB_RESPAWN_S = 300.0
ACE_RECENT_S = 12.0
LONG_DEATH_S = 15.0            # an enemy dead at least this long more counts for "push now"

#: (team, lane, tier) -> turret position in game units. Tiers: 1 outer, 2 inner, 3 inhibitor.
_TURRETS_GAME: dict[tuple[str, str, int], tuple[int, int]] = {
    ("ORDER", "top", 1): (981, 10441), ("ORDER", "top", 2): (1512, 6699), ("ORDER", "top", 3): (1169, 4287),
    ("ORDER", "mid", 1): (5846, 6396), ("ORDER", "mid", 2): (5048, 4812), ("ORDER", "mid", 3): (3651, 3696),
    ("ORDER", "bot", 1): (10504, 1029), ("ORDER", "bot", 2): (6919, 1483), ("ORDER", "bot", 3): (4281, 1253),
    ("CHAOS", "top", 1): (4318, 13875), ("CHAOS", "top", 2): (7943, 13411), ("CHAOS", "top", 3): (10481, 13650),
    ("CHAOS", "mid", 1): (8955, 8510), ("CHAOS", "mid", 2): (9767, 10113), ("CHAOS", "mid", 3): (11134, 11207),
    ("CHAOS", "bot", 1): (13866, 4505), ("CHAOS", "bot", 2): (13327, 8226), ("CHAOS", "bot", 3): (13624, 10572),
}
TURRET_UV: dict[tuple[str, str, int], tuple[float, float]] = {
    k: geometry.game_to_uv(x, y) for k, (x, y) in _TURRETS_GAME.items()}
NEXUS_UV = {"ORDER": geometry.game_to_uv(1550, 1660), "CHAOS": geometry.game_to_uv(13300, 13300)}
FOUNTAIN_UV = {"ORDER": geometry.BLUE_FOUNTAIN, "CHAOS": geometry.RED_FOUNTAIN}
_LANE_CODE = {"L": "top", "C": "mid", "R": "bot"}
#: Riot turret name number -> tier, per lane code (side lanes: 03 outer; mid: 05 outer).
_TIER = {("L", "03"): 1, ("L", "02"): 2, ("L", "01"): 3, ("R", "03"): 1, ("R", "02"): 2, ("R", "01"): 3,
         ("C", "05"): 1, ("C", "04"): 2, ("C", "03"): 3, ("C", "02"): 4, ("C", "01"): 4}
PHASE_FR = {"laning": "phase de voie", "mid": "milieu de partie", "late": "fin de partie",
            "end": "fin de partie décisive"}
OTHER = {"ORDER": "CHAOS", "CHAOS": "ORDER"}


def _f(x: Any, default: float | None = None) -> float | None:
    if x is None or isinstance(x, bool):
        return default
    try:
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        return default
    return v if math.isfinite(v) else default


def parse_turret(name: Any) -> tuple[str, str, int] | None:
    """``"Turret_T2_L_03_A"`` -> ``("CHAOS", "top", 1)`` (owner team, lane, tier; 4 = nexus)."""
    m = re.search(r"Turret_T([12])_([LCR])_(\d\d)", str(name or ""))
    if not m:
        return None
    tier = _TIER.get((m.group(2), m.group(3)))
    if tier is None:
        return None
    return ("ORDER" if m.group(1) == "1" else "CHAOS", _LANE_CODE[m.group(2)], tier)


def parse_inhib(name: Any) -> tuple[str, str] | None:
    """``"Barracks_T2_L1"`` -> ``("CHAOS", "top")``."""
    m = re.search(r"Barracks_T([12])_([LCR])", str(name or ""))
    if not m:
        return None
    return ("ORDER" if m.group(1) == "1" else "CHAOS", _LANE_CODE[m.group(2)])


def _norm(name: Any) -> str:
    s = str(name or "").split("#", 1)[0]
    return " ".join(s.split()).casefold()


def _team_lookup(game: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        players = game.all_players()
    except Exception:
        return out
    for p in players:
        team = getattr(p, "team", "")
        if team not in ("ORDER", "CHAOS"):
            continue
        for attr in ("riot_id", "summoner_name"):
            k = _norm(getattr(p, attr, ""))
            if k:
                out.setdefault(k, team)
    return out


def _killer_team(ev: dict, lookup: dict[str, str]) -> str | None:
    team = lookup.get(_norm(ev.get("KillerName")))
    if team:
        return team
    teams = {lookup.get(_norm(a)) for a in (ev.get("Assisters") or [])[:10] if isinstance(a, str)} - {None}
    return teams.pop() if len(teams) == 1 else None


@dataclass(frozen=True)
class DeadPlayer:
    alias: str
    name: str
    position: str
    respawn: float


@dataclass(frozen=True)
class MapState:
    gt: float = 0.0
    phase: str = "laning"
    my_team: str | None = None
    turrets_down: frozenset = frozenset()            # {(team, lane, tier)}
    inhibs_down: frozenset = frozenset()             # {(team, lane)} currently down
    dragons: dict = field(default_factory=dict)      # team -> count (elder excluded)
    soul_team: str | None = None                     # team that took the soul
    baron_team: str | None = None                    # team with the Baron buff now
    baron_left: float = 0.0
    elder_team: str | None = None
    elder_left: float = 0.0
    barons_taken: int = 0
    enemies_dead: tuple[DeadPlayer, ...] = ()
    allies_dead: tuple[DeadPlayer, ...] = ()         # without me
    me_dead: bool = False
    ace_team: str | None = None                      # team that aced in the last ACE_RECENT_S
    max_death_timer: float = 0.0

    @property
    def enemy_team(self) -> str | None:
        return OTHER.get(self.my_team or "")

    def standing_turrets(self, team: str | None) -> list[tuple[str, int, tuple[float, float]]]:
        """``(lane, tier, uv)`` of the standing turrets of ``team``."""
        return [(lane, tier, uv) for (tm, lane, tier), uv in TURRET_UV.items()
                if tm == team and (tm, lane, tier) not in self.turrets_down]

    def nearest_safe_uv(self, pos: tuple[float, float] | None) -> tuple[float, float] | None:
        """Nearest standing turret of my team (else my fountain) from ``pos``."""
        if self.my_team not in ("ORDER", "CHAOS"):
            return None
        pts = [uv for _l, _t, uv in self.standing_turrets(self.my_team)]
        if pos is None or not pts:
            return FOUNTAIN_UV[self.my_team]
        # prefer a turret that is not farther from my base than I am
        base = FOUNTAIN_UV[self.my_team]
        behind = [p for p in pts if geometry.dist(p, base) <= geometry.dist(pos, base) + 0.02]
        cand = behind or pts
        return min(cand, key=lambda p: geometry.dist(p, pos))

    def outer_down(self, team: str | None, lane: str | None) -> bool:
        return (team, lane, 1) in self.turrets_down

    def soul_point(self) -> str | None:
        """Team on soul point (3 dragons, soul not taken yet)."""
        if self.soul_team is not None:
            return None
        for team, n in self.dragons.items():
            if n >= 3:
                return team
        return None

    def enemies_dead_long(self, min_s: float = LONG_DEATH_S) -> list[DeadPlayer]:
        return [d for d in self.enemies_dead if d.respawn >= min_s]


def map_state(game: Any, gt: float | None = None, my_role: str | None = None) -> MapState:
    """Pure: the :class:`MapState` of one Live Client poll. Never raises."""
    try:
        return _map_state(game, gt, my_role)
    except Exception:
        log.debug("phase.map_state failed", exc_info=True)
        return MapState()


def _map_state(game: Any, gt: float | None, my_role: str | None) -> MapState:
    me = getattr(game, "me", None)
    team = geometry.normalize_team(getattr(me, "team", None)) if me is not None else None
    now = _f(gt) if gt is not None else _f(getattr(game, "game_time", None), 0.0)
    now = now or 0.0
    lookup = _team_lookup(game)
    turrets: set = set()
    inhib_t: dict[tuple[str, str], float] = {}
    dragons = {"ORDER": 0, "CHAOS": 0}
    soul = None
    baron_team, baron_t, barons = None, -1e9, 0
    elder_team, elder_t = None, -1e9
    ace_team = None
    for ev in getattr(game, "events", None) or []:
        if not isinstance(ev, dict):
            continue
        name = ev.get("EventName")
        T = _f(ev.get("EventTime"), 0.0) or 0.0
        if T > now + 2.0:
            continue
        if name == "TurretKilled":
            tur = parse_turret(ev.get("TurretKilled"))
            if tur is not None:
                turrets.add(tur)
        elif name == "InhibKilled":
            inh = parse_inhib(ev.get("InhibKilled"))
            if inh is not None:
                inhib_t[inh] = T
        elif name == "InhibRespawned":
            inh = parse_inhib(ev.get("InhibRespawned"))
            if inh is not None:
                inhib_t.pop(inh, None)
        elif name == "DragonKill":
            kt = _killer_team(ev, lookup)
            if str(ev.get("DragonType") or "").casefold() == "elder":
                elder_team, elder_t = kt, T
            elif kt in dragons:
                dragons[kt] += 1
                if dragons[kt] >= 4 and soul is None:
                    soul = kt
        elif name == "BaronKill":
            barons += 1
            baron_team, baron_t = _killer_team(ev, lookup), T
        elif name == "Ace":
            if now - T <= ACE_RECENT_S:
                at = str(ev.get("AcingTeam") or "").upper()
                ace_team = at if at in ("ORDER", "CHAOS") else _killer_team({"KillerName": ev.get("Acer")}, lookup)
    inhibs = frozenset(k for k, T in inhib_t.items() if now - T < INHIB_RESPAWN_S)
    baron_left = max(0.0, BARON_BUFF_S - (now - baron_t)) if baron_team else 0.0
    elder_left = max(0.0, ELDER_BUFF_S - (now - elder_t)) if elder_team else 0.0

    def dead(players: Any) -> tuple[DeadPlayer, ...]:
        out = []
        for p in players or []:
            if bool(getattr(p, "is_dead", False)):
                out.append(DeadPlayer(str(getattr(p, "champion_alias", "") or ""),
                                      str(getattr(p, "champion_name", "") or getattr(p, "champion_alias", "") or ""),
                                      str(getattr(p, "position", "") or "").upper(),
                                      max(0.0, _f(getattr(p, "respawn_timer", 0.0), 0.0) or 0.0)))
        return tuple(out)

    enemies_dead = dead(getattr(game, "enemies", None))
    allies_dead = dead(getattr(game, "allies", None))
    timers = [d.respawn for d in enemies_dead + allies_dead]
    if me is not None and bool(getattr(me, "is_dead", False)):
        timers.append(_f(getattr(me, "respawn_timer", 0.0), 0.0) or 0.0)
    max_timer = max(timers, default=0.0)
    # ---- phase
    outer_down = [k for k in turrets if k[2] == 1]
    my_lane = {"TOP": "top", "MIDDLE": "mid", "BOTTOM": "bot", "UTILITY": "bot"}.get(str(my_role or "").upper())
    lane_outer_down = my_lane is not None and any(k[1] == my_lane for k in outer_down)
    phase = "laning"
    if now >= LANING_END_GT or len(outer_down) >= 2 or (lane_outer_down and now >= 480.0):
        phase = "mid"
    if now >= LATE_GT or barons > 0:
        phase = "late"
    if (phase in ("mid", "late") and (inhibs or elder_team is not None
                                       or (soul is not None and now >= 1800.0)
                                       or (max_timer >= END_DEATH_TIMER_S and now >= 1500.0))):
        phase = "end"
    return MapState(gt=now, phase=phase, my_team=team, turrets_down=frozenset(turrets), inhibs_down=inhibs,
                    dragons=dragons, soul_team=soul, baron_team=baron_team if baron_left > 0 else None,
                    baron_left=baron_left, elder_team=elder_team if elder_left > 0 else None,
                    elder_left=elder_left, barons_taken=barons, enemies_dead=enemies_dead,
                    allies_dead=allies_dead, me_dead=bool(getattr(me, "is_dead", False)) if me else False,
                    ace_team=ace_team, max_death_timer=max_timer)


# ======================================================================================
# End-game / macro calls
# ======================================================================================
@dataclass(frozen=True)
class MacroCall:
    key: str
    text: str                       # French sentence (HUD / toast / voice)
    title: str                      # short banner title ("POUSSEZ", "BARON")
    priority: int = 50
    speak: bool = False             # important enough for the (budgeted) voice
    target: tuple[float, float] | None = None   # minimap arrow target
    color: str = "gold"             # banner style: "engage" (green) | "retreat" (red) | "gold"
    t: float = 0.0


CALL_COOLDOWN_S: dict[str, float] = {
    "ace": 45.0, "carries_dead": 60.0, "elder_soon": 120.0, "baron_ours": 200.0, "baron_theirs": 200.0,
    "soul_point": 150.0, "inhibs": 240.0, "our_inhib": 240.0, "phase": 1e9, "elder_ours": 160.0,
}
_CARRY_POS = ("BOTTOM", "MIDDLE")


def _plural(n: int, w: str) -> str:
    return f"{n} {w}{'s' if n > 1 else ''}"


class EndGameCaller:
    """Rare macro / end-game calls from :class:`MapState` + the objective timers. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._last: dict[str, float] = {}
            self._phase: str | None = None
            self._seen_baron = 0
            self._seen_elder: str | None = None

    def update(self, t: float, st: MapState, objectives: Any = None) -> list[MacroCall]:
        try:
            with self._lock:
                return self._update(float(t), st, list(objectives or []))
        except Exception:
            log.exception("EndGameCaller.update failed")
            return []

    def _ok(self, key: str, t: float) -> bool:
        last = self._last.get(key)
        return last is None or t - last >= CALL_COOLDOWN_S.get(key.split(":")[0], 90.0)

    def _update(self, t: float, st: MapState, objectives: list[Any]) -> list[MacroCall]:
        out: list[MacroCall] = []
        mine, theirs = st.my_team, st.enemy_team
        if mine is None or theirs is None:
            return out
        objs = {str(getattr(o, "key", "") or ""): o for o in objectives}

        def up(key: str, within: float = 0.0) -> bool:
            o = objs.get(key)
            if o is None:
                return False
            if getattr(o, "alive", False):
                return True
            rem = _f(getattr(o, "remaining", None))
            return rem is not None and 0.0 <= rem <= within

        def remaining(key: str) -> float | None:
            o = objs.get(key)
            return None if o is None or getattr(o, "alive", False) else _f(getattr(o, "remaining", None))

        # ---- phase change (written once per phase)
        if self._phase is not None and st.phase != self._phase:
            if st.phase == "mid":
                out.append(MacroCall("phase:mid", "Fin de la phase de voie : jouez groupés autour des "
                                     "objectifs, plus de farm seul loin de tes tours.", "MILIEU DE PARTIE", 40))
            elif st.phase == "late":
                out.append(MacroCall("phase:late", "Fin de partie : une erreur = Baron ou une inhib. "
                                     "Reste avec ton équipe et ne te fais pas attraper seul.", "FIN DE PARTIE", 45))
        self._phase = st.phase
        en_dead = list(st.enemies_dead)
        long_dead = st.enemies_dead_long()
        my_alive = not st.me_dead
        enemy_nexus = NEXUS_UV[theirs]
        # ---- ace / numbers advantage -> Baron or finish
        n = len(long_dead)
        if (st.ace_team == mine or n >= 4) and my_alive and len(st.allies_dead) <= 1:
            secs = int(min(d.respawn for d in long_dead)) if long_dead else int(min((d.respawn for d in en_dead), default=0))
            if st.inhibs_down & {(theirs, ln) for ln in ("top", "mid", "bot")} or st.phase == "end":
                txt = f"{_plural(max(n, len(en_dead)), 'ennemi')} morts pour {secs} s : poussez et finissez !"
                out.append(MacroCall("ace:end", txt, "FINISSEZ !", 95, True, enemy_nexus, "engage", t))
            elif up("baron") and st.gt >= 1200:
                out.append(MacroCall("ace:baron", f"{_plural(max(n, len(en_dead)), 'ennemi')} morts : Baron maintenant !",
                                     "BARON !", 93, True, (geometry.BARON_PIT[0], geometry.BARON_PIT[1]), "engage", t))
            elif up("elder") or up("dragon"):
                out.append(MacroCall("ace:dragon", f"{_plural(max(n, len(en_dead)), 'ennemi')} morts : prenez le dragon !",
                                     "DRAGON !", 90, True, (geometry.DRAGON_PIT[0], geometry.DRAGON_PIT[1]), "engage", t))
            else:
                out.append(MacroCall("ace:push", f"{_plural(max(n, len(en_dead)), 'ennemi')} morts : poussez une tour !",
                                     "POUSSEZ !", 88, True, None, "engage", t))
        else:
            carries = [d for d in long_dead if d.position in _CARRY_POS and d.respawn >= 30.0]
            if len(carries) >= 2 and my_alive and st.phase in ("mid", "late", "end"):
                secs = int(min(d.respawn for d in carries))
                who = " et ".join(d.name for d in carries[:2])
                out.append(MacroCall("carries_dead", f"Leurs carrys sont morts {secs} s ({who}) : poussez !",
                                     "POUSSEZ !", 85, True, None, "engage", t))
        # ---- Elder
        rem_el = remaining("elder")
        if rem_el is not None and 15.0 <= rem_el <= 40.0:
            out.append(MacroCall("elder_soon", f"Ancestral dans {int(round(rem_el / 5) * 5)} s : groupez-vous et "
                                 "ne vous faites pas attraper avant.", "ANCESTRAL", 80, True,
                                 (geometry.DRAGON_PIT[0], geometry.DRAGON_PIT[1]), "gold", t))
        if st.elder_team is not None and st.elder_team != self._seen_elder:
            self._seen_elder = st.elder_team
            if st.elder_team == mine:
                out.append(MacroCall("elder_ours", "Ancestral pris : engagez, ils sont exécutés sous le seuil !",
                                     "ANCESTRAL : ENGAGEZ", 92, True, None, "engage", t))
            else:
                out.append(MacroCall("elder_ours:theirs", "Ils ont l'ancestral : évitez le combat, défendez "
                                     "sous tour jusqu'à la fin du buff.", "ÉVITEZ LE COMBAT", 92, True,
                                     st.nearest_safe_uv(None), "retreat", t))
        # ---- Baron buff
        if st.barons_taken > self._seen_baron:
            self._seen_baron = st.barons_taken
            if st.baron_team == mine:
                out.append(MacroCall("baron_ours", "Baron pris : poussez groupés avec le buff, deux voies "
                                     "maximum, pas de combat inutile.", "BARON PRIS", 75, False, None, "engage", t))
            elif st.baron_team == theirs:
                out.append(MacroCall("baron_theirs", "Ils ont le Baron : restez sous vos tours, nettoyez "
                                     "les vagues, pas de combat dehors.", "BARON ENNEMI", 85, True,
                                     st.nearest_safe_uv(None), "retreat", t))
        # ---- soul point
        sp = st.soul_point()
        rem_dr = remaining("dragon")
        if sp is not None and (up("dragon") or (rem_dr is not None and rem_dr <= 60.0)):
            if sp == mine:
                out.append(MacroCall("soul_point", "Point d'âme : le prochain dragon donne l'âme, "
                                     "préparez-le tôt.", "POINT D'ÂME", 70, False,
                                     (geometry.DRAGON_PIT[0], geometry.DRAGON_PIT[1]), "gold", t))
            else:
                out.append(MacroCall("soul_point", "Point d'âme pour eux : contestez ce dragon groupés, "
                                     "ou échangez Baron / tours.", "ÂME EN JEU", 78, True,
                                     (geometry.DRAGON_PIT[0], geometry.DRAGON_PIT[1]), "gold", t))
        # ---- inhibitors
        theirs_down = sorted(ln for tm, ln in st.inhibs_down if tm == theirs)
        mine_down = sorted(ln for tm, ln in st.inhibs_down if tm == mine)
        if len(theirs_down) >= 2:
            out.append(MacroCall("inhibs", f"{len(theirs_down)} inhibiteurs ennemis tombés : Baron ou un ace "
                                 "et vous finissez. Pas de mort inutile.", "FINISSEZ", 70, False, None, "engage", t))
        if mine_down:
            out.append(MacroCall("our_inhib", "Votre inhibiteur est tombé : nettoyez les super sbires, "
                                 "ne combattez pas loin de la base.", "DÉFENDEZ", 72, False,
                                 st.nearest_safe_uv(None), "retreat", t))
        for c in sorted(out, key=lambda c: -c.priority):
            if self._ok(c.key, t):
                self._last[c.key] = t
                return [c]
        return []


__all__ = ["MapState", "map_state", "parse_turret", "parse_inhib", "TURRET_UV", "NEXUS_UV", "FOUNTAIN_UV",
           "EndGameCaller", "MacroCall", "PHASE_FR", "DeadPlayer"]
