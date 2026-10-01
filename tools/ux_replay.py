"""UX replay: what the player PERCEIVES, second by second, on scripted games + a severe judge.

Fast text loop (no screenshots): the REAL engine (:class:`treeaicoach.engine.CoachEngine`) runs on
scripted Live Client data + minimap icons (same wiring as :mod:`treeaicoach.coach_sim`, accelerated
clock), and every second the replay reads exactly what reaches the player:

* the compact HUD card (``overlay_render.compact_content``: colour + danger word + action line),
* the ONE top banner (``toasts.select_views`` on the overlay state's toasts / director banner),
* the voice lines, the danger beep, the play badges.

It prints a compact transcript (only changes)::

    03:12 | CARD[rouge] "GANK ! · Recule vers ta tour" | VOICE "Gank ! Lee Sin, recule !" | BEEP

then a JUDGE flags, with timestamps, everything a beginner would hate (abstract / long / English
texts, wrong colour, lane advice while dead, contradictions, flicker, silence before a death,
voice outside the whitelist, budgets). ``python -m tools.ux_replay --level debutant --scenario all``.
Development tool only: never imported by the application.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

UV = tuple[float, float]

# ======================================================================================
# Scripted games
# ======================================================================================

ROSTER = (
    # alias, name, team, position, smite
    ("Garen", "Garen", "ORDER", "TOP", False),
    ("Vi", "Vi", "ORDER", "JUNGLE", True),
    ("Lux", "Lux", "ORDER", "MIDDLE", False),
    ("Jinx", "Jinx", "ORDER", "BOTTOM", False),
    ("Thresh", "Thresh", "ORDER", "UTILITY", False),
    ("Darius", "Darius", "CHAOS", "TOP", False),
    ("LeeSin", "Lee Sin", "CHAOS", "JUNGLE", True),
    ("Ahri", "Ahri", "CHAOS", "MIDDLE", False),
    ("Caitlyn", "Caitlyn", "CHAOS", "BOTTOM", False),
    ("Nautilus", "Nautilus", "CHAOS", "UTILITY", False),
)
ALIASES = tuple(r[0] for r in ROSTER)
TEAM = {r[0]: r[2] for r in ROSTER}
POS = {r[0]: r[3] for r in ROSTER}
NAME = {r[0]: r[1] for r in ROSTER}

FOUNTAIN = {"ORDER": (0.04, 0.96), "CHAOS": (0.96, 0.04)}
#: laning spots (uv) per role and team (blue = ORDER bottom-left)
LANE_SPOT = {
    ("ORDER", "TOP"): (0.085, 0.27), ("CHAOS", "TOP"): (0.10, 0.17),
    ("ORDER", "MIDDLE"): (0.47, 0.53), ("CHAOS", "MIDDLE"): (0.53, 0.47),
    ("ORDER", "BOTTOM"): (0.80, 0.91), ("CHAOS", "BOTTOM"): (0.87, 0.84),
    ("ORDER", "UTILITY"): (0.78, 0.90), ("CHAOS", "UTILITY"): (0.86, 0.82),
    ("ORDER", "JUNGLE"): (0.25, 0.62), ("CHAOS", "JUNGLE"): (0.75, 0.38),
}
NEAR_UV = 0.09            # "next to me" (same as overlay_render.NEAR_ME_UV)


def _lerp(a: UV, b: UV, k: float) -> UV:
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)


def _keyframes(frames: list[tuple[float, Any]], gt: float) -> Any:
    """Linear interpolation between keyframes ``(gt, value)``; ``None`` values are steps
    (hidden), "default" means "use the default". Before the first frame: "default"."""
    if not frames or gt < frames[0][0]:
        return "default"
    for (t0, a), (t1, b) in zip(frames, frames[1:]):
        if t0 <= gt < t1:
            if isinstance(a, tuple) and isinstance(b, tuple) and t1 > t0:
                return _lerp(a, b, (gt - t0) / (t1 - t0)) if len(a) == 2 else a
            if isinstance(a, (int, float)) and isinstance(b, (int, float)) and t1 > t0:
                return a + (b - a) * (gt - t0) / (t1 - t0)
            return a
    return frames[-1][1]


@dataclass
class Scenario:
    name: str
    title: str
    t0: float                                     # game time of the first tick
    t1: float                                     # game time of the last tick
    me: str = "Garen"
    kills: list = field(default_factory=list)     # (gt, killer, victim, assisters)
    events: list = field(default_factory=list)    # (gt, EventName, killer, extra dict)
    paths: dict = field(default_factory=dict)     # alias -> [(gt, uv | None | "default")]
    hp: list = field(default_factory=list)        # my HP fraction keyframes
    xp: dict = field(default_factory=dict)        # alias -> xp rate
    items: dict = field(default_factory=dict)     # alias -> [(gt, [item ids])]
    gold: list = field(default_factory=list)      # my current gold keyframes
    late: bool = False                            # mid / late game default positions (grouped)
    # ---- truth for the judge
    danger: list = field(default_factory=list)    # (a, b, label): must be RED (gank / 2v1 / siege / ace)
    contacts: list = field(default_factory=list)  # (gt, label): a warning >= 3 s before
    need: list = field(default_factory=list)      # (a, b, label, regex, levels): a card / banner must say it
    moments: list = field(default_factory=list)   # [Moment]: game-changer moments (value judge)
    waves: dict = field(default_factory=dict)     # lane -> [(gt, meet)] keyframes (default: oscillating)
    warmup: float = 25.0                          # first seconds not judged (tracker warm-up)


@dataclass(frozen=True)
class Moment:
    """A scripted moment where a game-changer call is THE thing to say (VALUE judge): between ``a``
    and ``b`` a card / banner must match ``card`` (levels ``levels``), the voice must say ``voice``
    (levels ``voice_levels``), and no generic low-value line may take the card meanwhile."""

    a: float
    b: float
    label: str
    card: str
    voice: str | None = None
    levels: tuple = ("debutant", "intermediaire", "avance")
    voice_levels: tuple = ("debutant",)


#: "experience" (scripted seconds x rate) needed for each level 1..18
XP_LEVELS = (0, 35, 85, 150, 230, 325, 425, 530, 640, 755, 875, 1000, 1130, 1265, 1405, 1550, 1700, 1855)


def _death_s(gt: float) -> float:
    return 12.0 + gt / 45.0                       # ~17 s at 4:00, ~39 s at 20:00, ~45 s at 25:00


class ScriptGame:
    """Scripted Live Client data + minimap icons for one :class:`Scenario` (deterministic)."""

    def __init__(self, sc: Scenario) -> None:
        self.sc = sc
        self._pos_cache: tuple[float, dict] | None = None

    # ------------------------------------------------------------------ truth helpers
    def dead_until(self, alias: str, gt: float) -> float | None:
        for t, _k, v, _a in self.sc.kills:
            if v == alias and t <= gt < t + _death_s(t):
                return t + _death_s(t)
        return None

    def _default_pos(self, alias: str, gt: float) -> UV | None:
        team, pos = TEAM[alias], POS[alias]
        wob = 0.008 * math.sin(gt / 3.0 + ALIASES.index(alias))
        if self.sc.late:
            base = (0.45, 0.55) if team == "ORDER" else (0.58, 0.42)
            i = ALIASES.index(alias) % 5
            if team == "CHAOS" and (gt // 20 + i) % 3 == 0:
                return None
            return (base[0] + 0.02 * (i - 2) + wob, base[1] + 0.015 * ((i * 7) % 3 - 1))
        spot = LANE_SPOT[(team, pos)]
        if pos == "JUNGLE":
            if team == "CHAOS":
                return None                        # enemy jungler: in the fog unless scripted
            return (spot[0] + 0.05 * math.sin(gt / 20.0), spot[1] + 0.05 * math.cos(gt / 25.0))
        if team == "CHAOS" and pos != "TOP" and (gt // 25 + ALIASES.index(alias)) % 3 == 2:
            return None
        push = 0.02 * math.sin(gt / 45.0)
        if pos == "TOP":
            return (spot[0] + wob, spot[1] - push)
        return (spot[0] + wob, spot[1] + wob)

    def positions(self, gt: float) -> dict[str, UV | None]:
        c = self._pos_cache
        if c is not None and c[0] == gt:
            return c[1]
        out: dict[str, UV | None] = {}
        for a in ALIASES:
            if self.dead_until(a, gt) is not None:
                out[a] = None
                continue
            v = _keyframes(self.sc.paths.get(a, []), gt)
            out[a] = self._default_pos(a, gt) if v == "default" else v
        self._pos_cache = (gt, out)
        return out

    def hp(self, gt: float) -> float:
        if self.dead_until(self.sc.me, gt) is not None:
            return 0.0
        v = _keyframes(self.sc.hp, gt)
        return 0.85 if v == "default" else float(v)

    def level(self, alias: str, gt: float) -> int:
        """Monotonic level curve (rate 1: level 2 at 1:50, 3 at 2:40, 6 at 6:40, 11 at ~16:00)."""
        rate = self.sc.xp.get(alias, 0.85 if POS[alias] == "UTILITY" else 1.0)
        x = max(0.0, gt - 75.0) * rate
        return int(max(1, min(18, sum(1 for th in XP_LEVELS if x >= th))))

    def items(self, alias: str, gt: float) -> list[int]:
        rows = self.sc.items.get(alias)
        if rows is None:
            base = [1055] if POS[alias] in ("TOP", "BOTTOM") else [1056]
            if gt > 420:
                base.append(1001)
            if gt > 700:
                base.append(3044 if POS[alias] == "TOP" else 3020)
            if gt > 1100:
                base.append(3071 if POS[alias] == "TOP" else 3089)
            if gt > 1500:
                base.append(3047)
            return base
        out: list[int] = []
        for t, ids in rows:
            if gt >= t:
                out = list(ids)
        return out

    def near_counts(self, gt: float) -> tuple[int, int]:
        """(enemies, allies) within :data:`NEAR_UV` of me (truth, visible or not)."""
        pos = self.positions(gt)
        me = pos.get(self.sc.me)
        if me is None:
            return 0, 0
        e = a = 0
        for k, uv in pos.items():
            if k == self.sc.me or uv is None:
                continue
            if math.hypot(uv[0] - me[0], uv[1] - me[1]) <= NEAR_UV:
                if TEAM[k] != TEAM[self.sc.me]:
                    e += 1
                else:
                    a += 1
        return e, a

    def game_info(self, gt: float, t: float) -> Any:
        from treeaicoach.live_client import GameInfo, PlayerInfo

        sc = self.sc
        players: dict[str, Any] = {}
        for alias, name, team, pos, smite in ROSTER:
            rid = "Moi#EUW" if alias == sc.me else f"{alias}#SIM"
            du = self.dead_until(alias, gt)
            kills = sum(1 for k in sc.kills if k[1] == alias and k[0] <= gt)
            deaths = sum(1 for k in sc.kills if k[2] == alias and k[0] <= gt)
            assists = sum(1 for k in sc.kills if alias in k[3] and k[0] <= gt)
            cs_rate = {"TOP": 6.6, "MIDDLE": 7.0, "BOTTOM": 7.6, "JUNGLE": 5.4, "UTILITY": 1.0}[pos]
            cs = int(max(0.0, gt - 90.0) / 60.0 * cs_rate * (1.0 + 0.3 * (sc.xp.get(alias, 1.0) - 1.0)))
            players[alias] = PlayerInfo(
                riot_id=rid, summoner_name=rid, champion_alias=alias, champion_name=name, team=team,
                position=pos, is_dead=du is not None, respawn_timer=max(0.0, (du or gt) - gt),
                level=self.level(alias, gt), has_smite=smite, items=self.items(alias, gt),
                scores={"kills": kills, "deaths": deaths, "assists": assists, "creepScore": cs,
                        "wardScore": round(gt / 60.0 * (0.9 if pos != "UTILITY" else 2.0), 1)})
        names = {a: players[a].summoner_name for a in ALIASES}
        events: list[dict] = [{"EventID": 0, "EventName": "GameStart", "EventTime": 0.0}]
        rows: list[tuple[float, dict]] = []
        for tt, k, v, a in sc.kills:
            rows.append((tt, {"EventName": "ChampionKill", "KillerName": names[k], "VictimName": names[v],
                              "Assisters": [names[x] for x in a]}))
        for tt, ev, k, extra in sc.events:
            d = {"EventName": ev, "KillerName": names.get(k, k), "Assisters": [], "Stolen": "False"}
            d.update(extra)
            rows.append((tt, d))
        for i, (tt, d) in enumerate(sorted(rows, key=lambda r: r[0])):
            if tt <= gt:
                events.append(dict(d, EventID=i + 1, EventTime=tt))
        me = players[sc.me]
        mx = 700.0 + 95.0 * me.level
        g = _keyframes(sc.gold, gt)
        gold = (min(1400.0, 4.0 * (max(0.0, gt - 60.0) % 360.0)) if g == "default" else float(g)) if gt >= 1 else 0.0
        me.current_gold = gold
        my_team = TEAM[sc.me]
        return GameInfo(
            game_time=gt, game_mode="CLASSIC", map_number=11, map_terrain="Default", team_relative_colors=True,
            me=me, allies=[players[a] for a in ALIASES if a != sc.me and TEAM[a] == my_team],
            enemies=[players[a] for a in ALIASES if TEAM[a] != my_team], events=events,
            fetched_at=float(t), current_gold=gold,
            champion_stats={"currentHealth": round(mx * self.hp(gt), 1), "maxHealth": mx})

    def waves(self, gt: float) -> dict[str, Any]:
        from treeaicoach.waves import LaneWave

        push = 0.5 + 0.4 * math.sin(gt / 45.0)
        meet = round(0.3 + 0.45 * push, 3)
        fixed = _keyframes(self.sc.waves.get("top", []), gt)
        if fixed != "default" and fixed is not None:
            meet = round(float(fixed), 3)
        state = "pushing" if meet >= 0.58 else "pushed_in" if meet <= 0.42 else "even"
        return {"top": LaneWave("top", ally=5, enemy=4, meet=meet, state=state),
                "mid": LaneWave("mid", ally=4, enemy=4, meet=0.5, state="even"),
                "bot": LaneWave("bot", ally=5, enemy=3, meet=0.62, state="pushing")}


class _Source:
    is_demo = False

    def __init__(self, game: ScriptGame) -> None:
        import numpy as np

        self.game = game
        rng = np.random.default_rng(0)
        self.frame = (40 + rng.integers(0, 30, size=(200, 200, 3))).astype(np.uint8)
        self.gt = game.sc.t0

    def next(self, t: float) -> tuple[Any, Any]:
        self.gt = self.game.sc.t0 + t
        return self.frame, self.game.game_info(self.gt, t)


def _identified(game: ScriptGame, gt: float) -> list[Any]:
    from treeaicoach.detector import Detection
    from treeaicoach.identifier import Identified

    out = []
    my_team = TEAM[game.sc.me]
    for alias, uv in game.positions(gt).items():
        if uv is None:
            continue
        rel = "self" if alias == game.sc.me else ("ally" if TEAM[alias] == my_team else "enemy")
        probs = {"enemy": (0.9, 0.05, 0.05), "ally": (0.05, 0.9, 0.05), "self": (0.05, 0.05, 0.9)}[rel]
        det = Detection(u=uv[0], v=uv[1], r=0.03, score=0.95, cls=rel, cls_probs=probs, alias=alias)
        out.append(Identified(det=det, alias=alias, relation=rel, team=TEAM[alias], id_score=0.95))
    return out


class _Voice:
    backend = "sim"
    danger_voice = "bip_voix"

    def __init__(self) -> None:
        self.said: list[tuple[float, str]] = []
        self.beeps: list[tuple[float, str]] = []
        self.gt = 0.0

    def say(self, text: str, level: int = 1) -> None:
        self.said.append((self.gt, str(text)))

    def alert_beep(self, tone: str = "gank") -> None:
        self.beeps.append((self.gt, str(tone)))

    def set_danger_voice(self, v: Any) -> None:
        self.danger_voice = v

    def set_muted(self, on: bool) -> None:
        pass


# ======================================================================================
# Scenarios (Garen top, blue side, unless ``me`` says otherwise)
# ======================================================================================

ME_TOP = LANE_SPOT[("ORDER", "TOP")]
TOWER_TOP = (0.075, 0.40)               # my top outer tower (safe spot)


def _scenarios() -> dict[str, Scenario]:
    S: dict[str, Scenario] = {}
    S["laning"] = Scenario(
        "laning", "Début de partie et phase de voie top normale", 0.0, 240.0, warmup=0.0,
        paths={"Garen": [(0.0, FOUNTAIN["ORDER"]), (15.0, FOUNTAIN["ORDER"]), (60.0, ME_TOP), (61.0, "default")],
               "Darius": [(0.0, None), (60.0, "default")]})
    S["losing"] = Scenario(
        "losing", "Voie perdue contre un Darius plus fort", 150.0, 400.0,
        kills=[(170.0, "Darius", "Garen", [])],
        xp={"Darius": 1.35, "Garen": 0.9},
        items={"Darius": [(0, [1055]), (250, [1055, 1036, 1036]), (330, [1055, 3044])],
               "Garen": [(0, [1055])]},
        hp=[(150.0, 0.55), (200.0, 0.7), (260.0, 0.55), (320.0, 0.45), (400.0, 0.5)],
        paths={"Garen": [(150.0, ME_TOP), (230.0, FOUNTAIN["ORDER"]), (240.0, FOUNTAIN["ORDER"]),
                         (285.0, (0.08, 0.33)), (286.0, "default")]})
    lee_gank = [(150.0, None), (190.0, (0.33, 0.17)), (199.0, (0.14, 0.25)), (200.0, (0.11, 0.27)),
                (204.0, (0.09, 0.29)), (212.0, (0.08, 0.36)), (218.0, (0.20, 0.22)), (224.0, None)]
    S["gank_2v1"] = Scenario(
        "gank_2v1", "Gank du jungler top à 3:20 puis 2 contre 1 à 3:24 (53 % PV)", 150.0, 270.0,
        paths={"LeeSin": lee_gank,
               "Garen": [(150.0, "default"), (196.0, (0.088, 0.27)), (204.0, (0.083, 0.31)),
                         (214.0, TOWER_TOP), (240.0, TOWER_TOP), (241.0, "default")],
               "Darius": [(150.0, "default"), (196.0, (0.10, 0.22)), (204.0, (0.088, 0.30)),
                          (212.0, (0.085, 0.34)), (218.0, (0.10, 0.20)), (219.0, "default")]},
        hp=[(150.0, 0.8), (196.0, 0.75), (200.0, 0.62), (204.0, 0.53), (210.0, 0.38), (214.0, 0.34),
            (240.0, 0.36), (270.0, 0.5)],
        danger=[(198.0, 214.0, "gank Lee Sin"), (203.0, 214.0, "2 contre 1")],
        contacts=[(200.0, "Lee Sin au contact")])
    S["losing"] = Scenario(**{**S["losing"].__dict__, "moments": [
        Moment(316.0, 336.0, "Darius niveau 6 avant moi : recule", r"(?i)^recule|· recule",
               levels=("debutant", "intermediaire", "avance"))]})
    S["death"] = Scenario(
        "death", "Mort contre Darius puis réapparition", 210.0, 330.0,
        kills=[(250.0, "Darius", "Garen", ["LeeSin"])],
        paths={"Garen": [(210.0, "default"), (238.0, (0.09, 0.24)), (250.0, (0.09, 0.26)),
                         (266.0, FOUNTAIN["ORDER"]), (275.0, FOUNTAIN["ORDER"]), (310.0, (0.08, 0.40)),
                         (311.0, "default")],
               "Darius": [(210.0, "default"), (238.0, (0.10, 0.20)), (244.0, (0.09, 0.25)),
                          (252.0, (0.10, 0.20)), (253.0, "default")],
               "LeeSin": [(210.0, None), (240.0, (0.22, 0.20)), (246.0, (0.10, 0.25)), (254.0, (0.20, 0.20)),
                          (258.0, None)]},
        hp=[(210.0, 0.7), (238.0, 0.55), (244.0, 0.35), (249.0, 0.12), (250.0, 1.0)],
        danger=[(243.0, 249.5, "2 contre 1 avant la mort")],
        contacts=[(250.0, "mort")])
    S["dragon_top"] = Scenario(
        "dragon_top", "Dragon à 5:00, notre bot a la priorité, je suis top", 230.0, 340.0,
        paths={"LeeSin": [(230.0, None), (262.0, (0.70, 0.68)), (300.0, (0.67, 0.70)), (330.0, None)],
               "Vi": [(230.0, "default"), (270.0, (0.60, 0.76)), (330.0, (0.63, 0.73))],
               "Jinx": [(230.0, "default"), (280.0, (0.66, 0.79)), (330.0, (0.66, 0.79))],
               "Thresh": [(230.0, "default"), (280.0, (0.64, 0.78)), (330.0, (0.64, 0.78))]},
        events=[(322.0, "DragonKill", "Vi", {"DragonType": "Fire"})],
        need=[(263.0, 280.0, "leur jungler est en bas : jeu côté top", r"(?i)(pousse|tour|plaque|frappe|farm|pression|attaque|joue agressif)",
               ("debutant",))],
        moments=[Moment(262.0, 285.0, "leur jungler est vu en bas : joue agressif en haut",
                        r"(?i)joue agressif|frappe|plaque|mets la pression", r"(?i)jungler est en bas",
                        levels=("debutant", "intermediaire"))])
    S["dragon_jg"] = Scenario(
        "dragon_jg", "Dragon à 5:00 vu par mon jungler (Vi)", 220.0, 340.0, me="Vi",
        paths={"Vi": [(220.0, (0.30, 0.65)), (265.0, (0.60, 0.76)), (330.0, (0.63, 0.73))],
               "LeeSin": [(220.0, None), (275.0, (0.72, 0.66)), (330.0, None)]},
        events=[(322.0, "DragonKill", "Vi", {"DragonType": "Fire"})],
        need=[(240.0, 290.0, "dragon dans 60 s pour le jungler", r"(?i)dragon",
               ("debutant", "intermediaire", "avance"))])
    S["tower"] = Scenario(
        "tower", "Leur tour top tombe, puis rotation", 820.0, 920.0,
        events=[(850.0, "TurretKilled", "Garen", {"TurretKilled": "Turret_T2_L_03_A"})],
        paths={"Garen": [(820.0, (0.09, 0.15)), (850.0, (0.10, 0.12)), (880.0, (0.15, 0.10)),
                         (881.0, (0.15, 0.10))],
               "Darius": [(820.0, None)]},
        kills=[(815.0, "Garen", "Darius", [])],
        need=[(851.0, 880.0, "après la tour : prochaine action", r"(?i)\b(va|rejoins|rentre|pousse|aide|prends)\b",
               ("debutant", "intermediaire"))],
        moments=[Moment(820.0, 845.0, "Darius est mort : plaque la tour", r"(?i)plaque|frappe (la|leur) tour",
                        r"(?i)plaque|frappe la tour")])
    S["fight_won"] = Scenario(
        "fight_won", "Combat d'équipe gagné au milieu", 1180.0, 1290.0, late=True,
        paths={a: [(1180.0, "default"), (1200.0, (0.50 + 0.012 * (i - 5), 0.50 + 0.01 * ((i * 3) % 5 - 2))),
                   (1211.0, (0.50 + 0.012 * (i - 5), 0.50 + 0.01 * ((i * 3) % 5 - 2))),
                   (1222.0, (0.42 + 0.01 * i, 0.42) if TEAM[a] == "ORDER" else (0.80, 0.18)),
                   (1240.0, (0.38 + 0.01 * i, 0.36) if TEAM[a] == "ORDER" else (0.80, 0.18))]
               for i, a in enumerate(ALIASES)},
        kills=[(1206.0, "Garen", "Ahri", ["Lux"]), (1208.0, "Jinx", "Caitlyn", []),
               (1210.0, "Vi", "LeeSin", ["Garen"])],
        events=[(1100.0, "DragonKill", "Vi", {"DragonType": "Earth"})],
        hp=[(1180.0, 0.9), (1205.0, 0.7), (1215.0, 0.5), (1240.0, 0.55)],
        need=[(1211.0, 1240.0, "après le combat gagné (3 morts, Baron en vie) : Baron", r"(?i)baron",
               ("debutant", "intermediaire", "avance"))],
        moments=[Moment(1211.0, 1240.0, "3 ennemis morts, Baron en vie : Baron maintenant", r"(?i)baron",
                        r"(?i)morts : baron", voice_levels=("debutant", "intermediaire"))])
    S["fight_lost"] = Scenario(
        "fight_lost", "Combat d'équipe perdu, je suis bas", 1300.0, 1400.0, late=True,
        paths={**{a: [(1300.0, "default"), (1320.0, (0.48 + 0.012 * (i - 5), 0.52)), (1345.0, "default")]
                  for i, a in enumerate(ALIASES)},
               "Garen": [(1300.0, "default"), (1320.0, (0.47, 0.53)), (1336.0, (0.47, 0.53)),
                         (1350.0, (0.36, 0.66)), (1370.0, (0.30, 0.72))],
               "Darius": [(1300.0, "default"), (1320.0, (0.50, 0.51)), (1336.0, (0.49, 0.52)),
                          (1345.0, (0.42, 0.58)), (1350.0, None)],
               "Ahri": [(1300.0, "default"), (1320.0, (0.51, 0.50)), (1336.0, (0.48, 0.54)),
                        (1345.0, (0.43, 0.59)), (1350.0, None)]},
        kills=[(1326.0, "Darius", "Jinx", []), (1329.0, "Ahri", "Lux", []), (1332.0, "Caitlyn", "Vi", [])],
        hp=[(1300.0, 0.9), (1320.0, 0.6), (1332.0, 0.30), (1340.0, 0.22), (1370.0, 0.25)],
        danger=[(1334.0, 1345.0, "combat perdu, 2 contre 1")],
        contacts=[(1336.0, "Darius et Ahri sur moi")])
    base = (0.12, 0.88)
    S["siege_ace"] = Scenario(
        "siege_ace", "Ils assiègent notre base, puis ace", 1480.0, 1580.0, late=True,
        events=[(1500.0, "TurretKilled", "Darius", {"TurretKilled": "Turret_T1_C_03_A"}),
                (1512.0, "InhibKilled", "Darius", {"InhibKilled": "Barracks_T1_C1"}),
                (1530.0, "Ace", "Darius", {"Acer": "Darius#SIM", "AcingTeam": "CHAOS"})],
        paths={**{a: [(1480.0, (0.30 + 0.015 * i, 0.70 - 0.01 * i)), (1505.0, (0.18 + 0.01 * i, 0.82))]
                  for i, a in enumerate(ALIASES) if TEAM[a] == "CHAOS"},
               **{a: [(1480.0, (0.16 + 0.01 * i, 0.84)), (1500.0, (0.16 + 0.01 * i, 0.84))]
                  for i, a in enumerate(ALIASES) if TEAM[a] == "ORDER"},
               "Garen": [(1480.0, (0.15, 0.85)), (1500.0, base)]},
        kills=[(1515.0, "Darius", "Lux", []), (1518.0, "Ahri", "Jinx", []), (1521.0, "Caitlyn", "Thresh", []),
               (1525.0, "LeeSin", "Vi", []), (1529.0, "Darius", "Garen", ["Ahri"])],
        hp=[(1480.0, 0.9), (1510.0, 0.6), (1525.0, 0.25)],
        danger=[(1501.0, 1529.0, "siège de la base"), (1530.0, 1560.0, "ace")],
        contacts=[(1529.0, "mort au siège")])
    S["comeback"] = Scenario(
        "comeback", "Retour : on attrape Ahri seule puis Baron", 1620.0, 1720.0, late=True,
        kills=[(1640.0, "Garen", "Ahri", ["Vi"]), (1652.0, "Lux", "LeeSin", ["Vi"]),
               (1655.0, "Jinx", "Darius", ["Thresh"])],
        paths={"Ahri": [(1620.0, None), (1630.0, (0.40, 0.42)), (1640.0, (0.42, 0.44)), (1641.0, None)],
               "LeeSin": [(1620.0, "default"), (1650.0, None)], "Darius": [(1620.0, "default"), (1650.0, None)],
               "Garen": [(1620.0, "default"), (1632.0, (0.43, 0.47)), (1660.0, (0.40, 0.38)), (1700.0, (0.36, 0.32))],
               "Vi": [(1620.0, "default"), (1632.0, (0.41, 0.46)), (1660.0, (0.38, 0.36)), (1700.0, (0.35, 0.31))]},
        need=[(1656.0, 1690.0, "3 ennemis morts : Baron", r"(?i)baron", ("debutant", "intermediaire", "avance"))],
        moments=[Moment(1656.0, 1690.0, "3 ennemis morts : Baron maintenant", r"(?i)baron", r"(?i)morts : baron",
                        voice_levels=("debutant", "intermediaire"))])
    # ---------------------------------------------------------------- game-changer scenarios (VALUE judge)
    S["jungler_bot"] = Scenario(
        "jungler_bot", "Leur jungler est vu en bas (gank bot) pendant que je suis top", 420.0, 520.0,
        paths={"LeeSin": [(420.0, None), (452.0, (0.78, 0.86)), (466.0, (0.82, 0.84)), (470.0, None)]},
        waves={"top": [(420.0, 0.5)]},
        moments=[Moment(451.0, 472.0, "leur jungler est vu en bas : joue agressif en haut",
                        r"(?i)joue agressif|frappe|plaque|mets la pression", r"(?i)jungler est en bas",
                        levels=("debutant", "intermediaire"))])
    S["laner_recall"] = Scenario(
        "laner_recall", "Darius rentre en base pendant que ma vague pousse : plaques", 540.0, 620.0,
        paths={"Darius": [(540.0, "default"), (552.0, (0.11, 0.15)), (556.0, (0.12, 0.13)), (557.0, None),
                          (565.0, FOUNTAIN["CHAOS"]), (592.0, FOUNTAIN["CHAOS"]), (593.0, None)],
               "Garen": [(540.0, ME_TOP), (558.0, (0.09, 0.21)), (620.0, (0.09, 0.21))]},
        waves={"top": [(540.0, 0.66)]},
        moments=[Moment(566.0, 590.0, "Darius est rentré et ma vague pousse : prends les plaques",
                        r"(?i)plaque|frappe (la|leur) tour", r"(?i)plaque|frappe la tour")])
    fight3 = {a: [(1470.0, "default"), (1482.0, (0.50 + 0.012 * (i - 5), 0.50 + 0.01 * ((i * 3) % 5 - 2))),
                  (1493.0, (0.50 + 0.012 * (i - 5), 0.50 + 0.01 * ((i * 3) % 5 - 2))),
                  (1502.0, (0.42 + 0.01 * i, 0.42) if TEAM[a] == "ORDER" else (0.82, 0.16)),
                  (1520.0, (0.36 + 0.01 * i, 0.34) if TEAM[a] == "ORDER" else (0.82, 0.16))]
              for i, a in enumerate(ALIASES)}
    S["baron_3v0"] = Scenario(
        "baron_3v0", "Combat gagné 3 contre 0 à 24:50, Baron en vie", 1470.0, 1550.0, late=True,
        kills=[(1488.0, "Garen", "Ahri", ["Vi"]), (1490.0, "Jinx", "Caitlyn", ["Thresh"]),
               (1492.0, "Vi", "Nautilus", ["Lux", "Garen"])],
        paths=fight3, hp=[(1470.0, 0.9), (1488.0, 0.7), (1500.0, 0.65)],
        moments=[Moment(1494.0, 1520.0, "3 ennemis morts, Baron en vie : Baron maintenant", r"(?i)baron",
                        r"(?i)morts : baron", voice_levels=("debutant", "intermediaire"))])
    fed_paths = {
        "Garen": [(470.0, (0.09, 0.25)), (480.0, (0.09, 0.25)), (503.0, FOUNTAIN["ORDER"]), (512.0, FOUNTAIN["ORDER"]),
                  (545.0, ME_TOP), (546.0, "default"), (590.0, (0.09, 0.25)), (600.0, (0.09, 0.25)),
                  (626.0, FOUNTAIN["ORDER"]), (634.0, FOUNTAIN["ORDER"]), (662.0, ME_TOP), (670.0, (0.09, 0.25)),
                  (680.0, (0.09, 0.25)), (708.0, FOUNTAIN["ORDER"]), (728.0, FOUNTAIN["ORDER"]), (750.0, (0.08, 0.36))],
        "Darius": [(470.0, (0.10, 0.22)), (479.0, (0.09, 0.24)), (484.0, (0.10, 0.18)), (485.0, "default"),
                   (590.0, (0.10, 0.22)), (599.0, (0.09, 0.24)), (604.0, (0.10, 0.18)), (605.0, "default"),
                   (670.0, (0.10, 0.22)), (679.0, (0.09, 0.24)), (684.0, (0.10, 0.18)), (685.0, "default")]}
    S["fed_enemy"] = Scenario(
        "fed_enemy", "Darius 5/0 (3 fois sur moi) : achat défensif au retour", 470.0, 735.0,
        kills=[(480.0, "Darius", "Garen", []), (540.0, "Darius", "Vi", []), (600.0, "Darius", "Garen", ["LeeSin"]),
               (640.0, "Darius", "Lux", []), (680.0, "Darius", "Garen", [])],
        paths=fed_paths, xp={"Darius": 1.25, "Garen": 0.95},
        items={"Darius": [(0, [1055]), (500, [1055, 3044]), (620, [3071]), (700, [3071, 3047])],
               "Garen": [(0, [1055]), (510, [1055, 1001])]},
        gold=[(470.0, 600.0), (700.0, 1500.0), (726.0, 1500.0), (726.5, 350.0)],
        hp=[(470.0, 0.6), (479.0, 0.1), (479.9, 0.05), (480.0, 1.0), (590.0, 0.5), (599.0, 0.08), (599.9, 0.04),
            (600.0, 1.0), (670.0, 0.4), (679.0, 0.05), (679.9, 0.03), (680.0, 1.0)],
        danger=[(476.0, 479.5, "Darius sur moi à peu de vie"), (595.0, 599.5, "Darius sur moi à peu de vie"),
                (674.0, 679.5, "Darius sur moi à peu de vie")],
        contacts=[(480.0, "mort contre Darius"), (600.0, "mort contre Darius"), (680.0, "mort contre Darius")],
        moments=[Moment(708.0, 726.0, "Darius 5/0 : achat défensif au retour",
                        r"(?i)achète (cotte de mailles|armure d'étoffe)")])
    return S


SCENARIOS = _scenarios()
LEVELS = ("debutant", "intermediaire", "avance", "expert")


# ======================================================================================
# Run: what the player perceives
# ======================================================================================

@dataclass
class Frame:
    gt: float
    card: tuple[str, str] | None          # (colour, text) ; colour in vert / orange / rouge / gris
    card_lines: int = 0
    card_cut: bool = False
    banner: tuple | None = None            # (kind, text, title shape)
    voice: list = field(default_factory=list)    # [(text, key, level)]
    beep: list = field(default_factory=list)
    badge: list = field(default_factory=list)
    fight: bool = False
    dead: bool = False
    hp: float = 1.0
    near: tuple[int, int] = (0, 0)
    me_uv: UV | None = None


@dataclass
class Replay:
    scenario: Scenario
    level: str
    frames: list[Frame]
    seconds: float

    def transcript(self) -> list[str]:
        out = []
        prev_card = prev_banner = None
        for f in self.frames:
            parts = []
            if _shape(f.card) != prev_card:
                parts.append(f'CARD[{f.card[0]}] "{f.card[1]}"' if f.card else "CARD -")
                prev_card = _shape(f.card)
            if _shape(f.banner) != prev_banner:
                parts.append(f'BANNER "{f.banner[1]}"' if f.banner else "BANNER -")
                prev_banner = _shape(f.banner)
            for text, _k, _l in f.voice:
                parts.append(f'VOICE "{text}"')
            if f.beep:
                parts.append("BEEP")
            for b in f.badge:
                parts.append(f"BADGE {b}")
            if parts:
                truth = []
                if f.dead:
                    truth.append("mort")
                if f.fight:
                    truth.append("combat")
                if f.near[0]:
                    truth.append(f"{f.near[0]} ennemi(s) près")
                if not f.dead:
                    truth.append(f"PV {round(f.hp * 100)} %")
                out.append(f"{fmt(f.gt)} | " + " | ".join(parts) + f"   # {', '.join(truth)}")
        return out


def fmt(gt: float) -> str:
    return f"{int(gt // 60):02d}:{int(gt % 60):02d}"


_COLOUR = {"danger": "rouge", "careful": "orange", "ok": "vert"}


def _card_of(state: Any, now: float) -> tuple[tuple[str, str] | None, int, bool]:
    from treeaicoach import overlay_render as orr

    c = orr.compact_content(state, now)
    if c is None:
        return None, 0, False
    colour = _COLOUR.get(c["mode"], c["mode"])
    if c["mode"] == "ok" and tuple(c["colour"]) == tuple(orr.TAI_MUTED):
        colour = "gris"
    word, line = str(c.get("word") or ""), str(c.get("line") or "")
    text = " · ".join(x for x in (word, line) if x)
    try:
        lay = orr._compact_layout(state, 300, now)
        lines = lay["lines"]
        cut = any(str(x).endswith("…") for x in lines)
        n = len(lines) + (1 if lay["danger"] else 0)
    except Exception:
        n, cut = 1, False
    return (colour, text), n, cut


def run(scenario: str | Scenario, level: str = "debutant", hz: float = 2.0) -> Replay:
    import logging

    from treeaicoach import paths, skill, toasts as tst, waves as waves_mod
    from treeaicoach.config import Config
    from treeaicoach.engine import CoachEngine

    logging.disable(logging.WARNING)
    sc = SCENARIOS[scenario] if isinstance(scenario, str) else scenario
    home = tempfile.mkdtemp(prefix="treeai_ux_")
    os.environ[paths.ENV_HOME] = home
    paths._reset_cache()
    cfg = skill.apply(Config(), level)
    game = ScriptGame(sc)
    src = _Source(game)
    clock = [0.0]
    voice = _Voice()
    t_start = time.perf_counter()
    eng = CoachEngine(cfg, voice, frame_source=src, clock=lambda: clock[0], enable_hotkeys=False,
                      manage_overlay=False, recorder_factory=lambda: None)
    eng._vision = lambda frame: _identified(game, src.gt)          # type: ignore[method-assign]
    spoken: list[tuple[float, str, str, int, str]] = []            # (gt, text, key, level, kind)
    orig_speak = eng._speak_alerts

    def _speak(said: list, t: float, gt: float) -> None:
        for a in said:
            kind = getattr(a.kind, "value", str(a.kind))
            spoken.append((src.gt, str(a.text), str(a.key or ""), int(a.level), kind))
        orig_speak(said, t, gt)
    eng._speak_alerts = _speak                                        # type: ignore[method-assign]
    eng._ensure_components()
    patched = (waves_mod.WaveTracker.update, waves_mod.WaveTracker.waves)
    waves_mod.WaveTracker.update = lambda self, *a, **k: False              # type: ignore[method-assign]
    waves_mod.WaveTracker.waves = lambda self, *a, **k: game.waves(src.gt)  # type: ignore[method-assign]
    frames: list[Frame] = []
    n_said = n_beep = n_fx = 0
    steps = int((sc.t1 - sc.t0) * hz)
    try:
        for i in range(steps + 1):
            t = i / hz
            clock[0] = t
            voice.gt = sc.t0 + t
            eng.step(t)
            gt = src.gt
            st = eng._build_overlay_state(t)
            st.skill_level = level
            mono = time.monotonic()
            card, nlines, cut = _card_of(st, mono)
            views = tst.select_views(list(getattr(st, "toasts", None) or []), st, level)
            banner = None
            if views:
                v = views[0].toast
                txt = " : ".join(x for x in (str(v.title or "").strip(), str(v.subtitle or "").strip()) if x)
                banner = (str(v.kind), txt, re.sub(r"\d+", "#", str(v.title or "")))
            voice_now = []
            new_said = voice.said[n_said:]
            n_said = len(voice.said)
            keyed = [s for s in spoken if s[0] == gt]
            for _g, text in new_said:
                k = next((s for s in keyed if s[1] == text), None)
                voice_now.append((text, f"{k[4]}|{k[2]}" if k else "", k[3] if k else -1))
            beeps = [b for _g, b in voice.beeps[n_beep:]]
            n_beep = len(voice.beeps)
            fx = list(getattr(eng, "fx_pushed", []) or [])
            badges = [f"{getattr(p, 'title', None) or getattr(p, 'cls', '')}: {getattr(p, 'reason', '')}"
                      for p in fx[n_fx:]] if len(fx) >= n_fx else []
            n_fx = len(fx)
            tac = eng._tactics
            me_p = game.positions(gt).get(sc.me)
            frames.append(Frame(gt, card, nlines, cut, banner, voice_now, beeps, badges,
                                fight=bool(tac is not None and tac.in_fight()),
                                dead=game.dead_until(sc.me, gt) is not None, hp=game.hp(gt),
                                near=game.near_counts(gt), me_uv=me_p))
    finally:
        waves_mod.WaveTracker.update, waves_mod.WaveTracker.waves = patched   # type: ignore[method-assign]
        logging.disable(logging.NOTSET)
    return Replay(sc, level, frames, time.perf_counter() - t_start)


# ======================================================================================
# The judge
# ======================================================================================

@dataclass
class Violation:
    gt: float
    rule: str
    detail: str
    weight: int

    def line(self) -> str:
        return f"{fmt(self.gt)} [{self.rule}] {self.detail}"


#: verbs a card instruction may start with (French imperative, tutoiement)
VERBS = frozenset("""
va vas recule pousse rentre achète pose frappe joue reste attends défends farme prends aide regroupe
change évite tue retourne suis garde bloque contrôle place utilise vise arrête laisse tiens protège sors
cours fuis prépare lance engage attaque rejoins tourne ramasse récupère monte descends gèle fais ne
regarde surveille mets reviens profite plaque tape nettoie prends cache échange harcèle sécurise vole
continue termine finis avance repousse punis gagne réapparais dépense économise sauve suis groupe
balise baisse lâche enchaîne utilise rapproche-toi envahis regroupe-toi concentre-toi
""".split())
PLACES = re.compile(r"(?i)\b(top|mid|milieu|bot|haut|bas|tours?|tourelle|base|dragon|baron|héraut|larves|"
                    r"rivière|buisson|vague|sbires|jungle|camps?|nexus|inhibiteur|fontaine|voie|plaques?|balises?|"
                    r"objet|bottes|élixir|ancestral|équipe|alliés?|tireur|tank|ennemis?|adversaire|derrière|lampe|"
                    r"châtiment|téléportation|or|PO|achète|acheter)\b|"
                    + "|".join(re.escape(n) for n in NAME.values()))
ENGLISH = re.compile(r"(?i)\b(the|and|you|your|with|go|safe|push|back|recall|care|wave|lane|wards?|roam|"
                     r"farm|gank it|play)\b")
OBJ_WORD = re.compile(r"(?i)\b(dragon|baron|héraut|larves)\b")
OBJ_ADVICE = re.compile(r"(?i)(\b(va|prépare|aide|rejoins|prends|sécurise|regroupe|groupe|tape|fais|pose)\b[^:.]*"
                        r"\b(dragon|baron|héraut|larves)\b|\b(dragon|baron|héraut|larves)\b\s+(dans\s+)?"
                        r"(\d|une minute))")
LANE_ADVICE = re.compile(r"(?i)\b(vague|sbires|farm\w*|pousse|plaque|échange|harcèle|gèle|dernier coup|cs|"
                         r"joue agressif|reste sous ta tour|voie)\b")
_CLAUSE = r"(?i)(?:^|[:;,.!·] *|\bpuis )"
PUSH = re.compile(_CLAUSE + r"(pousse|plaque|frappe (la|leur) tour|attaque|engage|vas-y|joue agressif|punis|"
                  r"mets la pression|va taper)\b")
RETREAT = re.compile(_CLAUSE + r"(recule|reste sous ta tour|reste près de ta tour|arrête de pousser|ne pousse|fuis|"
                     r"joue prudent|ne (te )?bats pas|ne t'avance pas)\b")
RECALL = re.compile(r"(?i)\b(rentre en base|rentre acheter|retourne en base|achète maintenant|pousse puis rentre|"
                    r"rentre(?! pas))\b")
NO_RECALL = re.compile(r"(?i)\b(ne rentre pas|reste en voie|pas encore rentrer)\b")
VOUS = re.compile(r"(?i)\b(vous|votre|vos|(?!assez\b|chez\b|nez\b)\w+ez)\b")
TYPO = re.compile(r"  |\s[,.]|\.\.(?!\.)|\b(\w+) \1\b|\ba dire\b|[(][^)]*$")

#: budgets per game minute outside danger: (card changes, banners, voice lines)
BUDGET = {"debutant": (6, 2, 3), "intermediaire": (5, 2, 2), "avance": (4, 1, 2), "expert": (3, 1, 2)}

# ---- VALUE judge (does the line change what a beginner does in the next 10 s, is it the best now?)
#: generic / low-value lines: never on the card while a game-changer moment is on
GENERIC = re.compile(r"(?i)(sbires/min|par minute|score de vision|laisse ta tour taper|regarde la carte|"
                     r"avance seulement derrière|ne donne pas le premier sang|tape le plus proche|"
                     r"reste collé à ton tireur|farme jusqu'à|reste sur ta vague|pense à|"
                     r"prends les sbires sous ta tour|^farme prudemment|^joue prudent : reste sous ta tour|"
                     r"^pose ta balise|^balise le buisson|dépense tes)")
#: statistics / tutorial lines: never on a beginner's card at all
STATS = re.compile(r"(?i)(sbires/min|\d par minute|score de vision|laisse ta tour taper|regarde la carte pendant)")
#: non-danger voice lines per rolling minute, one topic per minute, length of a spoken line
VOICE_PER_MIN = 2
VOICE_TOPIC_S = 60.0
VOICE_MAX_CHARS = 42
#: where an instruction sends the player (card / banner agreement)
DEST = (("top", r"\bva top\b|\bvers le haut\b|\bva en haut\b|\bhéraut\b|\blarves\b"),
        ("bot", r"\bva bot\b|\bva en bas\b|\bdragon\b|\bancestral\b"),
        ("mid", r"\bva mid\b|\bau milieu\b|\bva au milieu\b"),
        ("baron", r"\bbaron\b"),
        ("base", r"\brentre\b|\bta base\b|\bfontaine\b|\bnexus\b"))
_OBJ_TIMER = re.compile(r"(?i)\b(baron|dragon|héraut|larves|ancestral)\b[^:]*?\bdans (?:(\d+):(\d\d)|(\d+) s)")


def _shape(card: Any) -> Any:
    """A card / banner without its live numbers (a ticking countdown is not a new card); a banner
    is identified by its kind + title (a live fight banner updating its subtitle is one banner)."""
    if card is None:
        return None
    if len(card) > 2 and card[2]:
        return (card[0], card[2])
    return (card[0], re.sub(r"\d+([,.]\d+)?", "#", card[1]))


def _is_alarm(card: Any) -> bool:
    """A red card, or an orange one led by a danger word ("LEE SIN ARRIVE · Recule vers ta tour")."""
    if card is None:
        return False
    head, sep, _rest = card[1].partition(" · ")
    return card[0] == "rouge" or (card[0] == "orange" and bool(sep) and head.isupper())


def _norm(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))


def _text_rules(gt: float, where: str, text: str, out: list[Violation], card: bool = False,
                lines: int = 0, cut: bool = False) -> None:
    if not text:
        return
    low = text.lower()
    if "—" in text:
        out.append(Violation(gt, "texte:tiret", f'{where} "{text}"', 2))
    if "pour cent" in low:
        out.append(Violation(gt, "texte:pour-cent", f'{where} "{text}"', 2))
    if "atakhan" in low:
        out.append(Violation(gt, "état:atakhan", f'{where} "{text}"', 5))
    m = ENGLISH.search(text)
    if m:
        out.append(Violation(gt, "texte:anglais", f'{where} "{text}" ({m.group(0)})', 2))
    if VOUS.search(text):
        out.append(Violation(gt, "texte:vouvoiement", f'{where} "{text}"', 2))
    if TYPO.search(text):
        out.append(Violation(gt, "texte:typo", f'{where} "{text}"', 1))
    if not card:
        return
    head, sep, rest = text.partition(" · ")
    line = rest if sep and head.isupper() else text
    if line == head and sep == "" and head.isupper() and len(head) <= 24 and ":" not in head:
        return                                    # the danger word alone ("GANK !")
    if len(line) > 60:
        out.append(Violation(gt, "texte:long", f'{where} "{line}" ({len(line)} car.)', 2))
    if lines > 3 or cut:
        out.append(Violation(gt, "texte:coupé", f'{where} "{line}" ({lines} lignes{", coupé" if cut else ""})', 2))
    first = re.split(r"[\s:,!']", line.strip(), 1)[0].lower()
    if first not in VERBS:
        out.append(Violation(gt, "texte:abstrait", f'{where} "{line}" (ne commence pas par un verbe)', 3))
    elif not PLACES.search(line):
        out.append(Violation(gt, "texte:abstrait", f'{where} "{line}" (ni lieu ni cible)', 3))


def judge(rp: Replay) -> list[Violation]:
    from treeaicoach import voice_policy as vp

    sc, lvl = rp.scenario, rp.level
    out: list[Violation] = []
    frames = [f for f in rp.frames if f.gt >= sc.t0 + sc.warmup]
    if not frames:
        return out

    def in_danger(gt: float, slack: float = 0.0) -> str | None:
        for a, b, label in sc.danger:
            if a - slack <= gt <= b + slack:
                return label
        return None

    seen_texts: set[tuple[str, str]] = set()
    # ---------------------------------------------------------------- text + state, per frame
    prev_card = None
    for f in frames:
        if f.card is not None and (_shape(f.card) != prev_card):
            key = ("card", _shape(f.card)[1])
            if key not in seen_texts:
                seen_texts.add(key)
                _text_rules(f.gt, "carte", f.card[1], out, card=True, lines=f.card_lines, cut=f.card_cut)
        prev_card = _shape(f.card)
        if f.banner is not None and ("banner", _norm(f.banner[1])) not in seen_texts:
            seen_texts.add(("banner", _norm(f.banner[1])))
            _text_rules(f.gt, "bandeau", f.banner[1], out)
        for text, _k, _l in f.voice:
            _text_rules(f.gt, "voix", text, out)
        colour = f.card[0] if f.card else None
        lab = in_danger(f.gt)
        if lab is not None and in_danger(f.gt - 1.0) is not None and colour != "rouge":
            out.append(Violation(f.gt, "état:pas-rouge", f"{lab} : carte {colour or 'absente'}"
                                 f"{' ' + repr(f.card[1]) if f.card else ''}", 5))
        if colour == "vert" and not f.dead:
            ne, na = f.near
            if ne >= 2:
                out.append(Violation(f.gt, "état:vert-entouré", f"carte verte avec {ne} ennemis près : {f.card[1]!r}", 5))
            elif f.hp < 0.35 and ne >= 1:
                out.append(Violation(f.gt, "état:vert-pv-bas", f"carte verte à {round(f.hp * 100)} % PV, "
                                     f"ennemi près : {f.card[1]!r}", 5))
        if f.card is not None and f.card[1].startswith("GANK") and f.near[1] >= 2 and f.near[1] + 1 >= f.near[0]:
            out.append(Violation(f.gt, "état:gank-en-groupe", f"« GANK, recule » avec {f.near[1]} alliés contre "
                                 f"{f.near[0]} ennemis autour : c'est un combat d'équipe", 3))
        texts = ([f.card[1]] if f.card else []) + ([f.banner[1]] if f.banner else [])
        for text in texts:
            line = text.split(" · ", 1)[-1]
            if LANE_ADVICE.search(line) and not RETREAT.search(line):
                if f.dead:
                    out.append(Violation(f.gt, "état:voie-mort", f"conseil de voie mort : {text!r}", 5))
                elif f.gt < 30.0:
                    out.append(Violation(f.gt, "état:voie-fontaine", f"conseil de voie avant 0:30 : {text!r}", 5))
            if OBJ_ADVICE.search(line):
                obj = OBJ_ADVICE.search(line).group(0).lower()
                key = next((k for k in ("dragon", "baron", "héraut", "larves") if k in obj), "dragon")
                key = {"héraut": "herald", "larves": "grubs"}.get(key, key)
                if not vp.objective_involved(f"objective_soon:{key}:60", POS[sc.me], f.me_uv, f.gt):
                    out.append(Violation(f.gt, "état:objectif-hors-rôle", f"{POS[sc.me]} : {text!r}", 4))
    # dedupe identical state violations (one per rule per 10 s)
    out = _squash(out)
    # ---------------------------------------------------------------- contradictions (10 s)
    shown: list[tuple[float, str, bool]] = []          # (gt, text, red)
    prev_c = prev_b = None
    for f in frames:
        if f.card is not None and _shape(f.card) != prev_c:
            shown.append((f.gt, f.card[1], _is_alarm(f.card)))
        if f.banner is not None and _shape(f.banner) != prev_b:
            shown.append((f.gt, f.banner[1], f.banner[0] in ("danger", "retreat")))
        prev_c, prev_b = _shape(f.card), _shape(f.banner)
        for text, k, lv in f.voice:
            shown.append((f.gt, text, lv >= 2 or k.split("|")[0] in ("jungler_approach", "roam_approach",
                                                                      "collapse")))
    for i, (t1, x1, _r1) in enumerate(shown):
        for t2, x2, r2 in shown[i + 1:]:
            if t2 - t1 > 10.0:
                break
            for a_re, b_re, name in ((PUSH, RETREAT, "pousse/recule"), (RECALL, NO_RECALL, "rentre/reste")):
                pair = (a_re.search(x1) and b_re.search(x2)) or (b_re.search(x1) and a_re.search(x2))
                if not pair:
                    continue
                if r2 and b_re is RETREAT and b_re.search(x2):
                    continue             # an alarm (new enemy information): retreating is the right reaction
                out.append(Violation(t2, "contradiction", f"{name} en {t2 - t1:.0f} s : {x1!r} puis {x2!r}", 4))
    # ---------------------------------------------------------------- flicker / repeats / banners
    changes: list[tuple[float, Any]] = [(frames[0].gt - 60.0, frames[0].card)]   # (state before the window)
    prev = _shape(frames[0].card)
    for f in frames:
        if _shape(f.card) != prev:
            changes.append((f.gt, f.card))
        prev = _shape(f.card)
    for (ta, ca), (tb, cb) in zip(changes, changes[1:]):
        danger_any = any(_is_alarm(c) for c in (ca, cb)) or in_danger(tb, 2.0)
        if not danger_any and tb - ta < 5.0:
            out.append(Violation(tb, "flicker:carte", f"carte changée {tb - ta:.0f} s après : "
                                 f"{(ca or ('', '-'))[1]!r} -> {(cb or ('', '-'))[1]!r}", 1))
    last_seen: dict[str, float] = {}
    cur = None
    calm_since: dict[str, bool] = {}          # topic -> a calm (non alarm) card was shown since it left
    for f in frames:
        c = _norm(f.card[1]) if f.card else None
        if c != cur and c is not None and not _is_alarm(f.card) and not in_danger(f.gt, 10.0):
            for k2 in calm_since:
                if k2 != c:
                    calm_since[k2] = True
        if c != cur and c is not None:
            k = _norm(c)
            if k in last_seen and f.gt - last_seen[k] < 40.0 and not _is_alarm(f.card) and calm_since.get(k):
                out.append(Violation(f.gt, "flicker:répétition", f"{f.card[1]!r} déjà montré il y a "
                                     f"{f.gt - last_seen[k]:.0f} s", 1))
        if cur is not None and c != cur:
            last_seen[cur] = f.gt
            calm_since[cur] = False
        cur = c
    banners: list[tuple[float, str, str]] = []
    pb = None
    for f in frames:
        if f.banner is not None and _shape(f.banner) != pb:
            banners.append((f.gt, f.banner[0], f.banner[1]))
        pb = _shape(f.banner)
    nd = [b for b in banners if b[1] not in ("danger", "retreat") and not in_danger(b[0])]
    for (ta, _ka, xa), (tb, _kb, xb) in zip(nd, nd[1:]):
        if tb - ta < 20.0:
            out.append(Violation(tb, "flicker:bandeaux", f"2 bandeaux en {tb - ta:.0f} s : {xa!r} puis {xb!r}", 2))
    bseen: dict[str, float] = {}
    for tb, _k, xb in banners:
        k = _norm(xb)
        if k in bseen and tb - bseen[k] < 40.0:
            out.append(Violation(tb, "flicker:répétition", f"bandeau {xb!r} répété après {tb - bseen[k]:.0f} s", 1))
        bseen[k] = tb
    # ---------------------------------------------------------------- silence where help was needed
    for tc, label in sc.contacts:
        if tc < sc.t0 + sc.warmup:
            continue
        warned = [f.gt for f in rp.frames if tc - 20.0 <= f.gt <= tc - 3.0 and (
            (f.card is not None and f.card[0] in ("rouge", "orange")) or f.beep
            or any(lv >= 1 for _x, _k, lv in f.voice) or (f.banner and f.banner[0] in ("danger", "retreat")))]
        if not warned:
            out.append(Violation(tc, "silence:contact", f"aucun avertissement >= 3 s avant : {label}", 5))
    for a, b, label, rx, levels in sc.need:
        if lvl not in levels:
            continue
        ok = any(a <= f.gt <= b and ((f.card is not None and re.search(rx, f.card[1]))
                                     or (f.banner is not None and re.search(rx, f.banner[1]))) for f in rp.frames)
        if not ok:
            out.append(Violation(a, "silence:moment-clé", f"rien entre {fmt(a)} et {fmt(b)} : {label}", 4))
    for a, b, label in sc.danger:
        if label.startswith(("ace",)):
            continue
        if not any(a - 10.0 <= f.gt <= b and f.beep for f in rp.frames):
            out.append(Violation(a, "voix:bip-manquant", f"pas de bip pendant : {label}", 4))
    out += judge_value(rp, frames)
    # ---------------------------------------------------------------- voice
    voice_level = {"debutant": "normal"}.get(lvl, "minimal")
    for f in frames:
        for text, key, lv in f.voice:
            ok = _voice_whitelisted(key, lv, voice_level, POS[sc.me], f.me_uv, f.gt, lvl)
            if not ok:
                out.append(Violation(f.gt, "voix:hors-liste", f"{text!r} (clé {key or '?'})", 3))
            if f.fight and lv < 2 and "call:retreat" not in key:
                out.append(Violation(f.gt, "voix:pendant-combat", f"{text!r}", 3))
            if lv >= 2 and not f.beep:
                out.append(Violation(f.gt, "voix:bip-manquant", f"danger dit sans bip : {text!r}", 4))
    # ---------------------------------------------------------------- budgets per minute
    bc, bb, bv = BUDGET.get(lvl, BUDGET["intermediaire"])
    per: dict[int, list[int]] = {}
    pc = pb2 = None
    for f in frames:
        m = int(f.gt // 60)
        row = per.setdefault(m, [0, 0, 0])
        quiet = not in_danger(f.gt, 3.0)
        if _shape(f.card) != pc and quiet and not _is_alarm(f.card) and not (pc is not None and _is_alarm(pc)):
            row[0] += 1
        if _shape(f.banner) != pb2 and f.banner is not None and quiet and f.banner[0] not in ("danger", "retreat"):
            row[1] += 1
        if quiet:
            row[2] += sum(1 for _x, _k, lv in f.voice if lv < 2)
        pc, pb2 = _shape(f.card), _shape(f.banner)
    for m, (c, b, v) in sorted(per.items()):
        for n, cap, what in ((c, bc, "changements de carte"), (b, bb, "bandeaux"), (v, bv, "phrases dites")):
            if n > cap:
                out.append(Violation(m * 60.0, "budget", f"minute {m}: {n} {what} (max {cap})", 2))
    out.sort(key=lambda v: v.gt)
    return out


def _dest(text: str) -> set[str]:
    """Places an instruction sends the player to (first clause only: "Va top : Héraut dans 1:00")."""
    head = str(text or "").split(" · ", 1)[-1].split(" : ", 1)[0].lower()
    if not re.match(r"(?i)^(va|rejoins|rentre|prends|aide|pousse ta vague puis va|regroupe)", head):
        return set()
    return {k for k, rx in DEST if re.search(rx, head)}


def _objective_truth(sc: Scenario, gt: float) -> dict[str, tuple[bool, float | None]]:
    """``key -> (alive, seconds before the spawn)`` from the 2026 timers + the scripted kills."""
    import json

    from treeaicoach import paths as _p  # noqa: F401  (assets path)

    try:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "treeaicoach", "assets", "objectives.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    out: dict[str, tuple[bool, float | None]] = {}
    kills = {"dragon": "DragonKill", "baron": "BaronKill", "herald": "HeraldKill"}
    for key, fr in (("dragon", "dragon"), ("baron", "baron"), ("herald", "héraut"), ("grubs", "larves")):
        row = data.get(key) or {}
        first = row.get("first")
        if first is None:
            continue
        spawn = float(first)
        for tt, ev, _k, _x in sorted(sc.events):
            if ev == kills.get(key) and tt <= gt and row.get("respawn"):
                spawn = tt + float(row["respawn"])
        despawn = row.get("despawn")
        if despawn is not None and gt >= float(despawn):
            continue
        out[fr] = (gt >= spawn, max(0.0, spawn - gt))
    return out


def judge_value(rp: Replay, frames: list[Frame]) -> list[Violation]:
    """The VALUE judge: missed game-changer moments (text and voice), generic lines shown while a
    game changer was due, statistics on a beginner's card, voice budget / topic repeats / length,
    card and banner sending the player to different places, stale objective countdowns, a death
    without its lesson."""
    sc, lvl = rp.scenario, rp.level
    out: list[Violation] = []
    in_danger = lambda gt: any(a <= gt <= b for a, b, _l in sc.danger)  # noqa: E731
    # ---- game-changer moments
    for m in sc.moments:
        win = [f for f in rp.frames if m.a <= f.gt <= m.b]
        hit = [f.gt for f in win if (f.card is not None and re.search(m.card, f.card[1]))
               or (f.banner is not None and re.search(m.card, f.banner[1]))]
        if lvl in m.levels:
            if not hit:
                out.append(Violation(m.a, "valeur:moment-manqué", f"rien entre {fmt(m.a)} et {fmt(m.b)} : {m.label}", 5))
            first = hit[0] if hit else m.b
            for f in win:
                if f.gt >= first:
                    break
                if f.card is not None and GENERIC.search(f.card[1].split(" · ", 1)[-1]):
                    out.append(Violation(f.gt, "valeur:générique", f"{f.card[1]!r} au lieu de : {m.label}", 3))
                    break
        if m.voice and lvl in m.voice_levels:
            said = any(re.search(m.voice, text) for f in rp.frames if m.a <= f.gt <= m.b + 2.0
                       for text, _k, _l in f.voice)
            if not said:
                out.append(Violation(m.a, "voix:moment-manqué", f"pas de voix entre {fmt(m.a)} et {fmt(m.b)} : "
                                                                 f"{m.label}", 4))
    # ---- statistics / tutorial lines on a beginner's card
    if lvl == "debutant":
        seen: set[str] = set()
        for f in frames:
            if f.card is not None and STATS.search(f.card[1]) and _norm(f.card[1]) not in seen:
                seen.add(_norm(f.card[1]))
                out.append(Violation(f.gt, "valeur:statistique", f"{f.card[1]!r} (ne change rien aux 10 s suivantes)", 2))
    # ---- voice: budget, topic repeats, length
    calm: list[tuple[float, str]] = []
    for f in frames:
        for text, key, lv in f.voice:
            if len(text) > VOICE_MAX_CHARS:
                out.append(Violation(f.gt, "voix:longue", f"{text!r} ({len(text)} car. > {VOICE_MAX_CHARS})", 2))
            kind = key.split("|", 1)[0]
            if lv >= 2 or kind in ("jungler_approach", "roam_approach", "collapse", "jungler_where") \
                    or "call:retreat" in key or "siege" in key:
                continue
            calm.append((f.gt, text))
    for i, (t1, x1) in enumerate(calm):
        n = sum(1 for t2, _x in calm if t1 <= t2 < t1 + 60.0)
        if n > VOICE_PER_MIN:
            out.append(Violation(t1, "voix:budget", f"{n} phrases (hors danger) en 60 s à partir de {fmt(t1)}", 2))
            break
        for t2, x2 in calm[i + 1:]:
            if t2 - t1 < VOICE_TOPIC_S and _norm(x2) == _norm(x1):
                out.append(Violation(t2, "voix:sujet-répété", f"{x2!r} déjà dit il y a {t2 - t1:.0f} s", 2))
    # ---- card and banner agree; objective countdowns are true
    told: set[tuple[str, str]] = set()
    stale: set[str] = set()
    for f in frames:
        if f.card is not None and f.banner is not None and f.banner[0] not in ("danger", "retreat") \
                and not _is_alarm(f.card):
            dc, db = _dest(f.card[1]), _dest(f.banner[1].split(" : ", 1)[-1])
            if dc and db and not (dc & db) and (f.card[1], f.banner[1]) not in told:
                told.add((f.card[1], f.banner[1]))
                out.append(Violation(f.gt, "incohérence:carte-bandeau", f"carte {f.card[1]!r} / bandeau "
                                                                          f"{f.banner[1]!r}", 4))
        truth = None
        for text in ([f.card[1]] if f.card else []) + ([f.banner[1]] if f.banner else []):
            mm = _OBJ_TIMER.search(text)
            if mm is None:
                continue
            truth = truth if truth is not None else _objective_truth(sc, f.gt)
            key = mm.group(1).lower()
            said = float(mm.group(4)) if mm.group(4) else 60.0 * float(mm.group(2)) + float(mm.group(3))
            alive, rem = truth.get(key, (False, None))
            if (alive or (rem is not None and abs(rem - said) > 15.0)) and _shape((None, text)) not in stale:
                stale.add(_shape((None, text)))
                out.append(Violation(f.gt, "état:objectif-périmé", f"{text!r} alors que {key} "
                                     f"{'est déjà là' if alive else f'apparaît dans {int(rem or 0)} s'}", 4))
    # ---- one concrete lesson per death (beginner / intermediate)
    if lvl in ("debutant", "intermediaire"):
        game = ScriptGame(sc)
        for tk, _k, v, _a in sc.kills:
            if v != sc.me or not (sc.t0 + sc.warmup <= tk <= sc.t1 - 8.0) or in_danger(tk) or in_danger(tk + 3.0):
                continue
            end = min(sc.t1, tk + _death_s(tk) - 1.0)
            ok = any(tk + 1.0 <= f.gt <= end and f.dead and f.card is not None and f.card[1] for f in rp.frames)
            if not ok and game.dead_until(sc.me, tk + 1.0) is not None:
                out.append(Violation(tk, "valeur:leçon-mort", "aucune leçon écrite pendant la mort", 3))
    return out


def _voice_whitelisted(key: str, level: int, voice_level: str, role: str, me_uv: Any, gt: float,
                       skill: str = "debutant") -> bool:
    from treeaicoach import game_changers as gcm
    from treeaicoach import voice_policy as vp

    kind, _, key = key.partition("|")
    if key.startswith("gc:"):                     # game changer: its class decides who hears it
        cls = key.split(":")[1] if key.count(":") >= 2 else ""
        return skill in gcm.VOICE_LEVELS.get(cls, frozenset())
    if kind in ("jungler_approach", "roam_approach", "collapse", "jungler_where"):
        return True
    if level >= 2 or key.startswith("call:retreat"):
        return True
    if key.startswith("objective_soon:"):
        lead = vp._objective_lead(key)
        return lead is not None and lead <= vp.OBJECTIVE_VOICE_MAX_LEAD_S and \
            vp.objective_involved(key, role, me_uv, gt)
    if key.startswith(vp.BIG_PRAISE_PREFIXES):
        return True
    if voice_level == "normal" and key.startswith(vp.BIG_CALL_PREFIXES):
        return True
    return False


def _squash(vs: list[Violation]) -> list[Violation]:
    """State violations: one per rule per 10 s window (a 12-second wrong colour is one bug)."""
    out: list[Violation] = []
    last: dict[str, float] = {}
    for v in sorted(vs, key=lambda v: v.gt):
        if v.rule.startswith("état:") and v.rule in last and v.gt - last[v.rule] < 10.0:
            continue
        last[v.rule] = v.gt
        out.append(v)
    return out


def score(vs: list[Violation]) -> int:
    return max(0, 100 - sum(v.weight for v in vs))


# ======================================================================================
# CLI
# ======================================================================================

def replay_all(levels: list[str], scenarios: list[str], out_dir: str | None = None, quiet: bool = False,
               stream: Any = None) -> dict[tuple[str, str], list[Violation]]:
    stream = stream or sys.stdout
    res: dict[tuple[str, str], list[Violation]] = {}
    for lvl in levels:
        for name in scenarios:
            rp = run(name, lvl)
            vs = judge(rp)
            res[(lvl, name)] = vs
            tr = rp.transcript()
            head = f"=== {lvl} / {name} : {rp.scenario.title} ({rp.seconds:.1f} s) score {score(vs)} " \
                   f"violations {len(vs)}"
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
                with open(os.path.join(out_dir, f"{lvl}_{name}.txt"), "w", encoding="utf-8") as fh:
                    fh.write(head + "\n" + "\n".join(tr) + "\n\nJUGE :\n" + "\n".join(v.line() for v in vs) + "\n")
            print(head, file=stream)
            if not quiet:
                for line in tr:
                    print("  " + line, file=stream)
                for v in vs:
                    print("  !! " + v.line(), file=stream)
    total = sum(len(v) for v in res.values())
    print(f"TOTAL violations {total} ; score moyen "
          f"{sum(score(v) for v in res.values()) / max(1, len(res)):.1f}", file=stream)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TreeAI Coach : relecture texte de ce que voit le joueur + juge")
    ap.add_argument("--level", default="debutant", help="debutant | intermediaire | avance | expert | all")
    ap.add_argument("--scenario", default="all", help="all | " + " | ".join(SCENARIOS))
    ap.add_argument("--out", default=None, help="dossier où écrire les transcriptions")
    ap.add_argument("--quiet", action="store_true", help="scores et violations seulement")
    a = ap.parse_args(argv)
    levels = list(LEVELS) if a.level == "all" else [a.level]
    names = list(SCENARIOS) if a.scenario == "all" else a.scenario.split(",")
    if a.quiet:
        res = replay_all(levels, names, a.out, quiet=True, stream=open(os.devnull, "w"))
        for (lvl, name), vs in res.items():
            print(f"{lvl:14s} {name:11s} score {score(vs):3d}  violations {len(vs)}")
            for v in vs:
                print("   " + v.line())
        print(f"TOTAL {sum(len(v) for v in res.values())}")
    else:
        replay_all(levels, names, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
