"""Fake Live Client event streams for the play-rating tests and demos (not a test module)."""

from __future__ import annotations

import copy
import dataclasses
from typing import Any

from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.plays import PlayClassifier, PlayContext

ME = "Moi#EUW"
ALLIES = [("Lee Sin", "LeeSin", "JUNGLE"), ("Ahri", "Ahri", "MIDDLE"), ("Jinx", "Jinx", "BOTTOM"),
          ("Thresh", "Thresh", "UTILITY")]
ENEMIES = [("Darius", "Darius", "TOP"), ("Vi", "Vi", "JUNGLE"), ("Zed", "Zed", "MIDDLE"),
           ("Caitlyn", "Caitlyn", "BOTTOM"), ("Lux", "Lux", "UTILITY")]


def _p(name: str, alias: str, pos: str, team: str, rid: str, smite: bool = False) -> PlayerInfo:
    return PlayerInfo(riot_id=rid, summoner_name=rid.split("#")[0], champion_alias=alias, champion_name=name,
                      team=team, position=pos, level=1, has_smite=smite)


class Sim:
    """A game in progress: players, event feed, my gold / health; ``snap()`` -> GameInfo."""

    def __init__(self) -> None:
        self.gt = 0.0
        self.me = _p("Garen", "Garen", "TOP", "ORDER", ME)
        self.allies = [_p(n, a, pos, "ORDER", f"Ally{i}#EUW", pos == "JUNGLE") for i, (n, a, pos) in enumerate(ALLIES)]
        self.enemies = [_p(n, a, pos, "CHAOS", f"Foe{i}#EUW", pos == "JUNGLE") for i, (n, a, pos) in enumerate(ENEMIES)]
        self.events: list[dict] = [{"EventID": 0, "EventName": "GameStart", "EventTime": 0.0}]
        self.gold = 500.0
        self.hp = (600.0, 600.0)
        self.fetch = 0

    def rid(self, alias: str) -> str:
        for p in [self.me] + self.allies + self.enemies:
            if p.champion_alias == alias:
                return p.riot_id.split("#")[0]
        return alias

    def player(self, alias: str) -> PlayerInfo:
        return next(p for p in [self.me] + self.allies + self.enemies if p.champion_alias == alias)

    def event(self, name: str, **kw: Any) -> dict:
        e = {"EventID": len(self.events), "EventName": name, "EventTime": self.gt, **kw}
        self.events.append(e)
        return e

    def kill(self, killer: str, victim: str, assisters: tuple[str, ...] = ()) -> dict:
        v = self.player(victim)
        v.is_dead, v.respawn_timer = True, 10.0 + self.gt / 60.0
        return self.event("ChampionKill", KillerName=self.rid(killer), VictimName=self.rid(victim),
                          Assisters=[self.rid(a) for a in assisters])

    def respawn_all(self) -> None:
        for p in [self.me] + self.allies + self.enemies:
            p.is_dead, p.respawn_timer = False, 0.0

    def snap(self) -> GameInfo:
        self.fetch += 1
        return GameInfo(game_time=self.gt, game_mode="CLASSIC", map_number=11,
                        me=copy.deepcopy(self.me), allies=copy.deepcopy(self.allies),
                        enemies=copy.deepcopy(self.enemies), events=list(self.events), fetched_at=float(self.fetch),
                        current_gold=self.gold,
                        champion_stats={"currentHealth": self.hp[0], "maxHealth": self.hp[1]})


def step(pc: PlayClassifier, sim: Sim, t: float | None = None, **ctx: Any) -> list:
    """One classifier tick at game time ``sim.gt`` (engine clock = game time unless ``t``)."""
    game = sim.snap()
    fields = {f.name for f in dataclasses.fields(PlayContext)}
    c = PlayContext(t=sim.gt if t is None else t, gt=sim.gt, game=game,
                    **{k: v for k, v in ctx.items() if k in fields})
    return pc.update(c)


def start(pc: PlayClassifier | None = None, gt: float = 60.0) -> tuple[PlayClassifier, Sim]:
    pc = pc or PlayClassifier()
    sim = Sim()
    sim.gt = gt
    step(pc, sim)            # first poll: baseline (the past is never rated)
    return pc, sim
