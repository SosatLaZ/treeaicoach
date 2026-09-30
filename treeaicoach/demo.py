"""Simulated game ("mode démo"): realistic minimaps + Live Client data for the real pipeline.

:class:`DemoSource` implements the engine's ``FrameSource`` protocol: ``next(t)`` returns a
minimap rendered with :mod:`treeaicoach.render` (official textures and champion icons, fog of
war, vision, minions, structures, wards, camera rectangle) and a :class:`GameInfo` built with
the :mod:`treeaicoach.live_client` dataclasses (French champion names). The engine runs the
REAL pipeline on it (detector, identifier, tracker, gank analysis, fog tracker, overlay), so
users can see and hear every feature without launching League of Legends.

Scenario (loops every :data:`DemoSource.SCENARIO_LENGTH` seconds, game clock 13:40 -> 14:55):

* I am Garen, ally (blue side, ``ORDER``) top laner, laning against Darius in the top lane;
* the enemy jungler Lee Sin (Smite) is spotted by a ward in his top-side jungle, walks out of
  vision at ~6.5 s (fog estimate), reappears in the top river at 33 s (jungler spotted), then
  ganks: WARNING, DANGER ~37 s, collapse with Darius ~39 s; I retreat under my tower, he backs
  off into the fog again at 47 s;
* allies (Vi, Lux, Jinx, Thresh) and enemies (Ahri, Caitlyn, Nautilus) play elsewhere;
* events: ``GameStart`` and an allied ``DragonKill`` at 13:55; the Rift Herald spawns at 15:00
  (objective timers speak at 14:00 and 14:40); my gold grows past the recall threshold.

:data:`DemoSource.GANK_WINDOW` is the interval (scenario seconds) that must contain the DANGER
gank alert; no gank alert (approach / roam / collapse) may be produced before it.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np

from treeaicoach import render as _render
from treeaicoach.live_client import GameInfo, PlayerInfo

log = logging.getLogger(__name__)

Key = tuple[float, float, float]           # (scenario second, u, v)

GAME_TIME_START = 820.0                     # scenario second 0 == 13:40 of game time
ICON_R = _render.ICON_RADIUS_DEFAULT
MY_TEAM = "ORDER"
ENEMY_TEAM = "CHAOS"


@dataclass(frozen=True)
class DemoChampion:
    """One scripted champion of the demo."""

    alias: str
    name_fr: str
    team: str
    position: str
    relation: str                           # "self" | "ally" | "enemy"
    path: tuple[Key, ...]                   # keyframes, linearly interpolated (looping)
    visible: tuple[tuple[float, float], ...] = ((0.0, 1e9),)   # intervals (enemies)
    smite: bool = False
    level: int = 10
    skin_id: int = 0
    wobble: float = 0.004                   # small idle motion amplitude
    summoner: str = ""


def _k(*pts: tuple[float, float, float]) -> tuple[Key, ...]:
    return tuple((float(s), float(u), float(v)) for s, u, v in pts)


# ------------------------------------------------------------------------------------ script
CHAMPIONS: tuple[DemoChampion, ...] = (
    DemoChampion(
        "Garen", "Garen", MY_TEAM, "TOP", "self", summoner="Toi", level=11,
        path=_k((0, 0.085, 0.205), (10, 0.090, 0.180), (20, 0.085, 0.200), (30, 0.088, 0.185),
                (37.5, 0.088, 0.190), (41.5, 0.072, 0.290), (45, 0.070, 0.300),
                (50, 0.085, 0.215), (60, 0.090, 0.185), (75, 0.085, 0.205)),
    ),
    DemoChampion(
        "Darius", "Darius", ENEMY_TEAM, "TOP", "enemy", summoner="Hache Rouge", level=11,
        path=_k((0, 0.095, 0.120), (10, 0.100, 0.108), (20, 0.093, 0.118), (30, 0.098, 0.110),
                (36.5, 0.096, 0.116), (39, 0.090, 0.165), (42.5, 0.082, 0.232),
                (45, 0.086, 0.212), (50, 0.095, 0.140), (56, 0.100, 0.112), (75, 0.095, 0.120)),
    ),
    DemoChampion(
        "LeeSin", "Lee Sin", ENEMY_TEAM, "JUNGLE", "enemy", smite=True, summoner="Moine Aveugle",
        wobble=0.0,
        path=_k((0, 0.475, 0.285), (4, 0.470, 0.280), (6.5, 0.455, 0.228), (12, 0.425, 0.195),
                (20, 0.420, 0.200), (26, 0.360, 0.260), (33, 0.290, 0.300),
                (36.5, 0.200, 0.250), (39.5, 0.130, 0.215), (42.5, 0.095, 0.270),
                (44, 0.100, 0.270), (47, 0.160, 0.252), (49, 0.200, 0.262),
                (55, 0.300, 0.320), (65, 0.420, 0.240), (75, 0.475, 0.285)),
        visible=((0.0, 6.5), (33.0, 47.0)),
    ),
    DemoChampion(
        "Vi", "Vi", MY_TEAM, "JUNGLE", "ally", smite=True, summoner="Poings d'Acier",
        path=_k((0, 0.250, 0.700), (12, 0.300, 0.760), (15, 0.330, 0.780), (30, 0.400, 0.820),
                (45, 0.300, 0.620), (60, 0.230, 0.560), (75, 0.250, 0.700)),
    ),
    DemoChampion(
        "Lux", "Lux", MY_TEAM, "MIDDLE", "ally", summoner="Lumière",
        path=_k((0, 0.440, 0.570), (15, 0.460, 0.545), (30, 0.430, 0.575), (45, 0.455, 0.550),
                (60, 0.440, 0.565), (75, 0.440, 0.570)),
    ),
    DemoChampion(
        "Ahri", "Ahri", ENEMY_TEAM, "MIDDLE", "enemy", summoner="Renarde",
        path=_k((0, 0.535, 0.470), (15, 0.520, 0.480), (30, 0.545, 0.455), (45, 0.525, 0.478),
                (60, 0.540, 0.462), (75, 0.535, 0.470)),
        visible=((0.0, 52.0), (60.0, 75.0)),
    ),
    DemoChampion(
        "Jinx", "Jinx", MY_TEAM, "BOTTOM", "ally", summoner="Canon Rose",
        path=_k((0, 0.790, 0.915), (20, 0.810, 0.920), (40, 0.780, 0.912), (60, 0.800, 0.918),
                (75, 0.790, 0.915)),
    ),
    DemoChampion(
        "Thresh", "Thresh", MY_TEAM, "UTILITY", "ally", summoner="Lanterne",
        path=_k((0, 0.800, 0.875), (20, 0.825, 0.880), (40, 0.790, 0.870), (60, 0.815, 0.878),
                (75, 0.800, 0.875)),
    ),
    DemoChampion(
        "Caitlyn", "Caitlyn", ENEMY_TEAM, "BOTTOM", "enemy", summoner="Shérif",
        path=_k((0, 0.905, 0.840), (20, 0.912, 0.825), (40, 0.900, 0.845), (60, 0.910, 0.830),
                (75, 0.905, 0.840)),
    ),
    DemoChampion(
        "Nautilus", "Nautilus", ENEMY_TEAM, "UTILITY", "enemy", summoner="Ancre",
        path=_k((0, 0.880, 0.905), (20, 0.890, 0.895), (40, 0.875, 0.910), (60, 0.885, 0.900),
                (75, 0.880, 0.905)),
        visible=((0.0, 22.0), (27.0, 75.0)),
    ),
)

# Allied wards: (u, v, until second). The river ward is swept by Lee Sin at 45.5 s.
WARDS: tuple[tuple[float, float, float], ...] = (
    (0.470, 0.285, 1e9),        # deep ward in the enemy top-side jungle (sees Lee at the start)
    (0.235, 0.285, 45.5),       # top river ward (spots Lee at 33 s)
    (0.700, 0.720, 1e9),        # dragon pit
)
WARD_ICON = "minimap_ward_green_full.png"

EVENT_DRAGON_S = 15.0           # allied DragonKill (scenario second)


def _interp(path: tuple[Key, ...], s: float) -> tuple[float, float]:
    """Position on a keyframe path at scenario second ``s`` (clamped to the path ends)."""
    if s <= path[0][0]:
        return path[0][1], path[0][2]
    for (s0, u0, v0), (s1, u1, v1) in zip(path, path[1:]):
        if s <= s1:
            a = 0.0 if s1 <= s0 else (s - s0) / (s1 - s0)
            return u0 + (u1 - u0) * a, v0 + (v1 - v0) * a
    return path[-1][1], path[-1][2]


def _visible(ch: DemoChampion, s: float) -> bool:
    return any(a <= s < b for a, b in ch.visible)


class DemoSource:
    """Simulated game implementing the engine ``FrameSource`` protocol. Thread-safe."""

    SCENARIO_LENGTH: float = 75.0
    #: Scenario seconds [start, end]: the DANGER gank alert must be produced inside, and no
    #: gank alert (jungler / roam approach, collapse) before ``start`` (Lee Sin reappears in
    #: the top river at 33 s, DANGER expected at ~37 s, collapse with Darius at ~39 s).
    GANK_WINDOW: tuple[float, float] = (33.0, 50.0)
    #: Scenario second at which the jungler walks into the fog (fog estimate expected).
    JUNGLER_HIDE_AT: float = 6.5
    #: Tells the engine not to record this fake game in the user's history.
    is_demo: bool = True

    def __init__(self, renderer: _render.MinimapRenderer | None = None, db: Any = None,
                 size: int = 280, seed: int = 0):
        self._renderer = renderer
        self._db = db
        try:
            self.size = int(max(64, min(1024, int(size))))
        except (TypeError, ValueError):
            self.size = 280
        self.seed = int(seed) if isinstance(seed, (int, np.integer)) else 0
        self._rng = np.random.default_rng(self.seed)
        self._phase = self._rng.uniform(0.0, 2.0 * math.pi, size=(len(CHAMPIONS), 2))
        self._lock = threading.Lock()
        self._t0: float | None = None
        self._icons: dict[str, np.ndarray | None] | None = None
        self.texture = _render.DEFAULT_TEXTURE

    # ------------------------------------------------------------------ helpers
    def reset(self) -> None:
        """Restart the scenario at the next call of :meth:`next`."""
        with self._lock:
            self._t0 = None

    def scenario_time(self, t: float) -> float:
        """Scenario second (``0 .. SCENARIO_LENGTH``) for engine time ``t``."""
        with self._lock:
            try:
                tf = float(t)
            except (TypeError, ValueError):
                tf = 0.0
            if not math.isfinite(tf):
                tf = 0.0
            if self._t0 is None or tf < self._t0:
                self._t0 = tf
            return (tf - self._t0) % self.SCENARIO_LENGTH

    def _ensure_assets(self) -> None:
        if self._renderer is None:
            self._renderer = _render.MinimapRenderer()
        if self._db is None:
            from treeaicoach.champions import get_default_db

            self._db = get_default_db()
        if self._icons is None:
            icons: dict[str, np.ndarray | None] = {}
            for ch in CHAMPIONS:
                try:
                    icons[ch.alias] = self._db.load_icon(ch.alias, ch.skin_id)
                except Exception:
                    log.debug("Demo icon unavailable for %s", ch.alias, exc_info=True)
                    icons[ch.alias] = None
            self._icons = icons

    def _name(self, ch: DemoChampion) -> str:
        try:
            entry = self._db.get(ch.alias) if self._db is not None else None
            if entry is not None and entry.name_fr:
                return str(entry.name_fr)
        except Exception:
            pass
        return ch.name_fr

    def positions(self, s: float) -> dict[str, tuple[float, float, bool]]:
        """Ground truth at scenario second ``s``: alias -> (u, v, visible on the minimap)."""
        out: dict[str, tuple[float, float, bool]] = {}
        for i, ch in enumerate(CHAMPIONS):
            u, v = _interp(ch.path, s)
            if ch.wobble:
                pu, pv = self._phase[i]
                u += ch.wobble * math.sin(0.9 * s + pu)
                v += ch.wobble * math.sin(1.3 * s + pv)
            vis = True if ch.relation != "enemy" else _visible(ch, s)
            out[ch.alias] = (float(u), float(v), vis)
        return out

    # ------------------------------------------------------------------ scene
    def _minions(self, s: float) -> list[tuple[float, float, str]]:
        out: list[tuple[float, float, str]] = []
        osc = 0.012 * math.sin(s * 0.35)
        # top lane wave between Garen and Darius (left edge, below the bend)
        for i in range(4):
            out.append((0.083 + 0.004 * (i % 2), 0.172 + 0.013 * i + osc, "ally"))
            out.append((0.090 - 0.004 * (i % 2), 0.160 - 0.013 * i + osc, "enemy"))
        # mid lane wave
        for i in range(4):
            d = 0.013 * i
            out.append((0.470 - d * 0.7 + osc, 0.530 + d * 0.7 - osc, "ally"))
            out.append((0.505 + d * 0.7 + osc, 0.495 - d * 0.7 - osc, "enemy"))
        # bot lane wave
        for i in range(4):
            out.append((0.835 + 0.013 * i - osc, 0.915 - 0.004 * (i % 2), "ally"))
            out.append((0.870 + 0.013 * i - osc, 0.905 + 0.004 * (i % 2), "enemy"))
        return out

    def scene(self, s: float) -> _render.Scene:
        """The :class:`render.Scene` of scenario second ``s``."""
        self._ensure_assets()
        icons = self._icons or {}
        pos = self.positions(s)
        vision: list[tuple[float, float, float]] = []
        champs: list[_render.ChampionSprite] = []
        me_uv = (0.5, 0.5)
        for ch in CHAMPIONS:
            u, v, vis = pos[ch.alias]
            if ch.relation == "self":
                me_uv = (u, v)
            if ch.relation != "enemy":
                vision.append((u, v, _render.VISION_R["champion"]))
            elif vis:
                vision.append((u, v, 0.03))
            if not vis:
                continue
            champs.append(_render.ChampionSprite(u=u, v=v, r=ICON_R, relation=ch.relation,
                                                 icon=icons.get(ch.alias)))
        # enemies drawn last within their area is not guaranteed in game: keep list order,
        # but make sure my icon is not systematically on top (no priority for "self").
        for su, sv, _kind, team in _render.STRUCTURES:
            if team == MY_TEAM:
                vision.append((su, sv, _render.VISION_R["turret"]))
        minions = self._minions(s)
        for mu, mv, team in minions:
            if team == "ally":
                vision.append((mu, mv, _render.VISION_R["minion"]))
        wards = []
        for wu, wv, until in WARDS:
            if s < until:
                wards.append((wu, wv, WARD_ICON))
                vision.append((wu, wv, _render.VISION_R["ward"]))
        cw, chh = _render.CAMERA_SIZE
        # the in-game camera rectangle never leaves the map
        cu = min(max(me_uv[0], cw / 2), 1.0 - cw / 2)
        cv = min(max(me_uv[1], chh / 2), 1.0 - chh / 2)
        camera = (cu - cw / 2, cv - chh / 2, cu + cw / 2, cv + chh / 2)
        return _render.Scene(
            texture=self.texture, size=self.size, fog_alpha=_render.FOG_ALPHA_DEFAULT,
            vision=vision, champions=champs, minions=minions, structures=True, wards=wards,
            pings=[], camera=camera, camps=True, my_team=MY_TEAM,
        )

    # ------------------------------------------------------------------ game info
    def _player(self, ch: DemoChampion, s: float, dead: bool = False) -> PlayerInfo:
        spells = ("Châtiment", "Saut éclair") if ch.smite else ("Saut éclair", "Téléportation")
        summoner = ch.summoner or ch.alias
        scores = {"kills": 2, "deaths": 1, "assists": 3, "creepScore": 95 + int(s // 12),
                  "wardScore": 9.0}
        if ch.relation == "self":
            scores = {"kills": 3, "deaths": 1, "assists": 2, "creepScore": 104 + int(s // 10),
                      "wardScore": 11.0}
        return PlayerInfo(
            riot_id=f"{summoner}#DEMO", summoner_name=summoner, champion_alias=ch.alias,
            champion_name=self._name(ch), team=ch.team, position=ch.position,
            is_dead=dead, respawn_timer=0.0, level=ch.level, skin_id=ch.skin_id,
            has_smite=ch.smite, is_bot=False, spells=spells,
            items=[1054, 3047, 1028, 2055] if ch.relation == "self" else [1055, 3006],
            scores=scores,
            current_gold=self.my_gold(s) if ch.relation == "self" else 0.0,
        )

    @staticmethod
    def my_gold(s: float) -> float:
        """My current gold at scenario second ``s`` (crosses 1300 at 50 s)."""
        return float(min(2400.0, 700.0 + 12.0 * s))

    def game_info(self, s: float, t: float) -> GameInfo:
        """The Live Client snapshot of scenario second ``s`` (``fetched_at`` = ``t``)."""
        players = {ch.alias: self._player(ch, s) for ch in CHAMPIONS}
        me = next(players[c.alias] for c in CHAMPIONS if c.relation == "self")
        allies = [players[c.alias] for c in CHAMPIONS if c.relation == "ally"]
        enemies = [players[c.alias] for c in CHAMPIONS if c.relation == "enemy"]
        game_time = GAME_TIME_START + s
        events: list[dict] = [{"EventID": 0, "EventName": "GameStart", "EventTime": 0.0}]
        if s >= EVENT_DRAGON_S:
            vi = players["Vi"]
            events.append({
                "EventID": 1, "EventName": "DragonKill", "EventTime": GAME_TIME_START + EVENT_DRAGON_S,
                "DragonType": "Fire", "Stolen": "False", "KillerName": vi.summoner_name,
                "Assisters": [],
            })
        return GameInfo(
            game_time=game_time, game_mode="CLASSIC", map_number=11, map_terrain="Default",
            team_relative_colors=True, me=me, allies=allies, enemies=enemies, events=events,
            fetched_at=float(t), current_gold=me.current_gold,
        )

    # ------------------------------------------------------------------ FrameSource
    def next(self, t: float) -> tuple[np.ndarray | None, GameInfo | None]:
        """(minimap BGR, game info) at engine time ``t``. Never raises."""
        try:
            s = self.scenario_time(t)
        except Exception:
            log.exception("DemoSource: invalid time %r", t)
            return None, None
        game: GameInfo | None = None
        try:
            game = self.game_info(s, t)
        except Exception:
            log.exception("DemoSource: cannot build the game info")
        try:
            self._ensure_assets()
            frame = self._renderer.render(self.scene(s))  # type: ignore[union-attr]
        except Exception:
            log.exception("DemoSource: rendering failed")
            frame = None
        return frame, game


def jungler_alias() -> str:
    """Alias of the demo's enemy jungler."""
    return next(c.alias for c in CHAMPIONS if c.relation == "enemy" and c.smite)
