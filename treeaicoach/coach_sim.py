"""Full-game coaching simulator: how much does the coach say / show per minute?

The 75 s demo (:mod:`treeaicoach.demo`) shows every feature once; it cannot tell whether the
coach is *spammy* over a whole game. :class:`SimGame` scripts a complete 30-minute game
(Live Client data: levels, items, gold, CS, HP, kills / deaths, objectives, towers; minimap
positions of the ten champions with fog) and :func:`run` drives the REAL engine on it with an
accelerated clock (no rendering: the detector stage is replaced by the scripted icons), then
counts what reached the player:

* ``voice``  - lines spoken (gank alerts, calls, budgeted messages, hype lines);
* ``toasts`` - top-centre banners pushed (:class:`treeaicoach.toasts.ToastQueue`);
* ``tips``   - changes of the ONE written HUD advice line;
* ``texts``  - written-only messages (HUD line + toast routed by the voice gate).

``python -m treeaicoach.coach_sim [--level debutant|intermediaire|avance|expert] [--minutes 30]``
prints the per-minute rates and the lines themselves (for review). Development tool only:
never imported by the application.
"""

from __future__ import annotations

import argparse
import math
import random
import re
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from treeaicoach.live_client import GameInfo, PlayerInfo

ME = "Moi#EUW"


@dataclass
class _P:
    alias: str
    name: str
    team: str
    pos: str
    rel: str
    xp_rate: float = 1.0
    smite: bool = False
    items: list[int] = field(default_factory=list)


ROSTER = (
    _P("Garen", "Garen", "ORDER", "TOP", "self", 1.0),
    _P("Vi", "Vi", "ORDER", "JUNGLE", "ally", 1.0, smite=True),
    _P("Lux", "Lux", "ORDER", "MIDDLE", "ally", 1.0),
    _P("Jinx", "Jinx", "ORDER", "BOTTOM", "ally", 0.95),
    _P("Thresh", "Thresh", "ORDER", "UTILITY", "ally", 0.85),
    _P("Darius", "Darius", "CHAOS", "TOP", "enemy", 1.07),
    _P("LeeSin", "Lee Sin", "CHAOS", "JUNGLE", "enemy", 1.0, smite=True),
    _P("Ahri", "Ahri", "CHAOS", "MIDDLE", "enemy", 1.02),
    _P("Caitlyn", "Caitlyn", "CHAOS", "BOTTOM", "enemy", 0.97),
    _P("Nautilus", "Nautilus", "CHAOS", "UTILITY", "enemy", 0.85),
)

#: (game time, killer alias, victim alias, assisters)
KILLS = (
    (250.0, "Darius", "Garen", ["LeeSin"]),
    (470.0, "Garen", "Darius", []),
    (610.0, "Jinx", "Caitlyn", ["Thresh"]),
    (760.0, "Ahri", "Lux", []),
    (905.0, "LeeSin", "Garen", ["Darius"]),
    (1120.0, "Garen", "Ahri", ["Vi"]),
    (1125.0, "Vi", "LeeSin", ["Garen"]),
    (1390.0, "Caitlyn", "Jinx", ["Nautilus"]),
    (1500.0, "Lux", "Darius", ["Garen", "Vi"]),
    (1640.0, "Darius", "Garen", ["Ahri", "Nautilus"]),
)
#: (game time, event name, killer alias, extra)
OBJECTIVES = (
    (330.0, "DragonKill", "LeeSin", {"DragonType": "Fire"}),
    (500.0, "HordeKill", "Vi", {}),          # grubs spawn at 8:00 (2026)
    (660.0, "DragonKill", "Vi", {"DragonType": "Earth"}),
    (930.0, "HeraldKill", "LeeSin", {}),
    (980.0, "TurretKilled", "Darius", {"TurretKilled": "Turret_T1_L_03_A"}),
    (1010.0, "DragonKill", "Vi", {"DragonType": "Water"}),
    (1320.0, "DragonKill", "LeeSin", {"DragonType": "Air"}),
    (1560.0, "BaronKill", "Vi", {}),
    (1600.0, "TurretKilled", "Garen", {"TurretKilled": "Turret_T2_L_03_A"}),
)
#: my recalls (game time): purchases happen at the end of each
MY_BACKS = (300.0, 560.0, 840.0, 1180.0, 1450.0)
MY_BUYS = {300.0: [1036, 1001], 560.0: [3044], 840.0: [3071, 3047], 1180.0: [3053], 1450.0: [3742]}
OPP_BUYS = {290.0: [1036], 520.0: [3044], 780.0: [3071], 1100.0: [3053, 3047], 1400.0: [3742]}
DEATH_S = 20.0
RECALL_S = 40.0          # recall + walk back


def _xp_level(gt: float, rate: float) -> int:
    """Rough level curve: 2 at ~1:45, 6 at ~7:00, 11 at ~16:00, 16 at ~27:00 (x rate)."""
    x = max(0.0, gt - 75.0) * rate
    lvl = 1 + int(math.sqrt(x / 7.5)) if x < 400 else 6 + int((x - 400) / 110)
    return int(max(1, min(18, lvl)))


class SimGame:
    """Scripted Live Client data + minimap icons of one full game (deterministic per seed)."""

    def __init__(self, seed: int = 0, length_s: float = 1800.0) -> None:
        self.rng = random.Random(seed)
        self.length_s = float(length_s)
        self._vis_seed = seed

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _dead_until(alias: str, gt: float) -> float | None:
        for t, _k, v, _a in KILLS:
            if v == alias and t <= gt < t + DEATH_S + t / 120.0:
                return t + DEATH_S + t / 120.0
        return None

    @staticmethod
    def _recalling(gt: float) -> bool:
        return any(b <= gt < b + RECALL_S for b in MY_BACKS)

    def _items(self, p: _P, gt: float) -> list[int]:
        if p.alias == "Garen":
            out = [1055]
            for b, items in MY_BUYS.items():
                if gt >= b + 12.0:
                    out += items
        elif p.alias == "Darius":
            out = [1055]
            for b, items in OPP_BUYS.items():
                if gt >= b:
                    out += items
        else:
            out = [1056] + ([3020] if gt > 600 else []) + ([3089] if gt > 1000 else [])
        if gt > 1200 and 3044 in out and 3071 in out:
            out.remove(3044)             # Phage was built into Black Cleaver
        if gt > 1200 and 1036 in out:
            out.remove(1036)
        return out[:6]

    def _gold(self, gt: float) -> float:
        last = max([0.0] + [b + 12.0 for b in MY_BACKS if b + 12.0 <= gt])
        return min(4000.0, 120.0 + 6.5 * (gt - last))

    def positions(self, gt: float) -> dict[str, tuple[float, float] | None]:
        """Minimap uv of each champion (None = not visible to my team)."""
        out: dict[str, tuple[float, float] | None] = {}
        wob = lambda a: 0.01 * math.sin(gt / 3.0 + a)          # noqa: E731
        for i, p in enumerate(ROSTER):
            dead = self._dead_until(p.alias, gt) is not None
            if dead:
                out[p.alias] = None
                continue
            ally = p.team == "ORDER"
            if p.alias == "Garen":
                if self._recalling(gt):
                    out[p.alias] = (0.05, 0.95)
                elif gt > 1500:
                    out[p.alias] = (0.42 + wob(i), 0.58)          # grouped mid
                else:
                    push = 0.5 + 0.4 * math.sin(gt / 45.0)
                    out[p.alias] = (0.082 + wob(i), 0.32 - 0.12 * push)
                continue
            if p.alias == "Darius":
                if gt > 1500:
                    vis = (gt // 20) % 3 != 0
                    out[p.alias] = (0.55, 0.45) if vis else None
                else:
                    me = out.get("Garen") or (0.082, 0.25)
                    vis = not (gt % 300 < 35)                      # recalls
                    out[p.alias] = (0.095 + wob(i), max(0.08, me[1] - 0.07)) if vis else None
                continue
            if p.alias == "LeeSin":
                phase = (gt % 90.0) / 90.0
                vis = (gt // 15) % 4 == 1
                side = "top" if (gt // 90) % 2 == 0 else "bot"
                u, v = (0.25 + 0.1 * phase, 0.2 + 0.1 * phase) if side == "top" else (0.7, 0.75 - 0.1 * phase)
                out[p.alias] = (u, v) if vis else None
                continue
            lane = {"MIDDLE": (0.5, 0.5), "BOTTOM": (0.85, 0.9), "UTILITY": (0.83, 0.88), "JUNGLE": (0.3, 0.6)}
            base = lane.get(p.pos, (0.5, 0.5))
            if not ally:
                vis = ((gt + 7 * i) // 25) % 3 != 2
                out[p.alias] = (base[0] + wob(i), base[1] - 0.05 + wob(i + 1)) if vis else None
            else:
                out[p.alias] = (base[0] - 0.04 + wob(i), base[1] + 0.03)
        return out

    def waves(self, gt: float) -> dict[str, Any]:
        """Scripted minion waves (waves.LaneWave per lane, ``s`` from my base): my top wave follows
        my push cycle; in mid game an enemy wave regularly reaches the empty bot lane."""
        from treeaicoach.waves import LaneWave

        push = 0.5 + 0.4 * math.sin(gt / 45.0)
        meet = round(0.3 + 0.45 * push, 3)
        state = "pushing" if meet >= 0.58 else "pushed_in" if meet <= 0.42 else "even"
        out = {"top": LaneWave("top", ally=5, enemy=4, meet=meet, state=state),
               "mid": LaneWave("mid", ally=4, enemy=4, meet=0.5, state="even")}
        bot_meet = 0.33 if gt > 1000 and (gt // 60) % 3 == 0 else 0.55
        out["bot"] = LaneWave("bot", ally=3, enemy=6 if bot_meet < 0.4 else 4, meet=bot_meet,
                              state="pushed_in" if bot_meet < 0.42 else "even")
        return out

    def game_info(self, gt: float, t: float) -> GameInfo:
        players: dict[str, PlayerInfo] = {}
        for p in ROSTER:
            rid = ME if p.rel == "self" else f"{p.alias}#SIM"
            dead_until = self._dead_until(p.alias, gt)
            kills = sum(1 for k in KILLS if k[1] == p.alias and k[0] <= gt)
            deaths = sum(1 for k in KILLS if k[2] == p.alias and k[0] <= gt)
            assists = sum(1 for k in KILLS if p.alias in k[3] and k[0] <= gt)
            cs_rate = {"TOP": 6.6, "MIDDLE": 7.0, "BOTTOM": 7.6, "JUNGLE": 5.4, "UTILITY": 1.0}[p.pos]
            if p.alias == "Darius":
                cs_rate = 7.2
            cs = int(max(0.0, gt - 90.0) / 60.0 * cs_rate)
            players[p.alias] = PlayerInfo(
                riot_id=rid, summoner_name=rid, champion_alias=p.alias, champion_name=p.name, team=p.team,
                position=p.pos, is_dead=dead_until is not None,
                respawn_timer=max(0.0, (dead_until or gt) - gt), level=_xp_level(gt, p.xp_rate),
                has_smite=p.smite, items=self._items(p, gt),
                scores={"kills": kills, "deaths": deaths, "assists": assists, "creepScore": cs,
                        "wardScore": round(gt / 60.0 * (0.9 if p.pos != "UTILITY" else 2.0), 1)})
        names = {p.alias: players[p.alias].summoner_name for p in ROSTER}
        events: list[dict] = [{"EventID": 0, "EventName": "GameStart", "EventTime": 0.0}]
        eid = 1
        rows: list[tuple[float, dict]] = []
        for tt, k, v, a in KILLS:
            rows.append((tt, {"EventName": "ChampionKill", "KillerName": names[k], "VictimName": names[v],
                              "Assisters": [names[x] for x in a]}))
        for tt, name, k, extra in OBJECTIVES:
            d = {"EventName": name, "KillerName": names[k], "Assisters": [], "Stolen": "False"}
            d.update(extra)
            rows.append((tt, d))
        for tt, d in sorted(rows, key=lambda r: r[0]):
            if tt <= gt:
                events.append(dict(d, EventID=eid, EventTime=tt))
                eid += 1
        me = players["Garen"]
        dead = me.is_dead
        hp_frac = 0.0 if dead else 0.45 + 0.5 * (0.5 + 0.5 * math.sin(gt / 23.0))
        if any(0 <= tt - gt < 6 for tt, _k, v, _a in KILLS if v == "Garen"):
            hp_frac = 0.18
        if self._recalling(gt):
            hp_frac = 1.0
        mx = 700.0 + 95.0 * me.level
        gold = 0.0 if gt < 1 else self._gold(gt)
        me.current_gold = gold
        return GameInfo(
            game_time=gt, game_mode="CLASSIC", map_number=11, map_terrain="Default", team_relative_colors=True,
            me=me, allies=[players[p.alias] for p in ROSTER if p.rel == "ally"],
            enemies=[players[p.alias] for p in ROSTER if p.rel == "enemy"], events=events,
            fetched_at=float(t), current_gold=gold,
            champion_stats={"currentHealth": round(mx * hp_frac, 1), "maxHealth": mx})


class _Source:
    """FrameSource: a neutral (non-black) minimap + the scripted game info."""

    is_demo = False

    def __init__(self, sim: SimGame, gt0: float = 0.0) -> None:
        self.sim = sim
        self.gt0 = gt0
        rng = np.random.default_rng(0)
        self.frame = (40 + rng.integers(0, 30, size=(200, 200, 3))).astype(np.uint8)
        self.gt = gt0

    def next(self, t: float) -> tuple[np.ndarray, GameInfo]:
        self.gt = self.gt0 + t
        return self.frame, self.sim.game_info(self.gt, t)


def _identified(sim: SimGame, gt: float) -> list[Any]:
    from treeaicoach.detector import Detection
    from treeaicoach.identifier import Identified

    out = []
    for p in ROSTER:
        uv = sim.positions(gt).get(p.alias)
        if uv is None:
            continue
        cls = {"self": "self", "ally": "ally", "enemy": "enemy"}[p.rel]
        probs = {"enemy": (0.9, 0.05, 0.05), "ally": (0.05, 0.9, 0.05), "self": (0.05, 0.05, 0.9)}[cls]
        det = Detection(u=uv[0], v=uv[1], r=0.03, score=0.95, cls=cls, cls_probs=probs, alias=p.alias)
        out.append(Identified(det=det, alias=p.alias, relation=p.rel, team=p.team, id_score=0.95))
    return out


class _Voice:
    backend = "sim"

    def __init__(self) -> None:
        self.said: list[tuple[float, str]] = []
        self.gt = 0.0

    def say(self, text: str, level: int = 1) -> None:
        self.said.append((self.gt, str(text)))

    def set_muted(self, on: bool) -> None:
        pass


@dataclass
class SimResult:
    minutes: float
    voice: list[tuple[float, str]]
    toasts: list[tuple[float, str, str]]
    tips: list[tuple[float, str]]
    texts: list[tuple[float, str, str]]
    extras: dict = field(default_factory=dict)       # engine.coach_extras() at the end

    def rates(self) -> dict[str, float]:
        m = max(self.minutes, 1e-6)
        return {"voice/min": round(len(self.voice) / m, 2), "toasts/min": round(len(self.toasts) / m, 2),
                "tips/min": round(len(self.tips) / m, 2), "texts/min": round(len(self.texts) / m, 2)}

    def genie_rate(self) -> float:
        """COUPS DE GÉNIE macro calls per minute."""
        return round(len(self.extras.get("genie") or []) / max(self.minutes, 1e-6), 2)

    def busiest_minute(self) -> int:
        """Most messages (voice + toasts + tip changes) in one game minute."""
        cnt: dict[int, int] = {}
        for gt, *_ in list(self.voice) + list(self.toasts) + list(self.tips):
            cnt[int(gt // 60)] = cnt.get(int(gt // 60), 0) + 1
        return max(cnt.values(), default=0)


def run(level: str = "intermediaire", minutes: float = 30.0, hz: float = 4.0, seed: int = 0,
        cfg_changes: dict | None = None) -> SimResult:
    """Run the engine on a scripted game; returns everything the player saw / heard."""
    import os

    from treeaicoach import paths, skill
    from treeaicoach.config import Config
    from treeaicoach.engine import CoachEngine

    home = tempfile.mkdtemp(prefix="treeai_sim_")
    os.environ[paths.ENV_HOME] = home
    paths._reset_cache()
    cfg = skill.apply(Config(), level)
    if cfg_changes:
        import dataclasses
        cfg = dataclasses.replace(cfg, **cfg_changes)
    sim = SimGame(seed, minutes * 60.0)
    src = _Source(sim)
    clock = [0.0]
    voice = _Voice()
    eng = CoachEngine(cfg, voice, frame_source=src, clock=lambda: clock[0], enable_hotkeys=False,
                      manage_overlay=False, recorder_factory=lambda: None)
    eng._vision = lambda frame: _identified(sim, src.gt)          # type: ignore[method-assign]
    toasts: list[tuple[float, str, str]] = []
    tips: list[tuple[float, str]] = []
    prev_tip = None
    steps = int(minutes * 60.0 * hz)
    eng._ensure_components()
    from treeaicoach import waves as waves_mod
    patched = (waves_mod.WaveTracker.update, waves_mod.WaveTracker.waves)
    waves_mod.WaveTracker.update = lambda self, *a, **k: False              # type: ignore[method-assign]
    waves_mod.WaveTracker.waves = lambda self, *a, **k: sim.waves(src.gt)   # type: ignore[method-assign]
    q = eng._toasts
    if q is not None:
        push = q.push

        def _push(kind: str, title: str, subtitle: str, **kw: Any) -> Any:
            toasts.append((src.gt, str(title), str(subtitle)))
            return push(kind, title, subtitle, **kw)
        q.push = _push                                              # type: ignore[method-assign]
    try:
        for i in range(steps):
            t = i / hz
            clock[0] = t
            voice.gt = src.gt
            eng.step(t)
            tip = eng.top_tip()
            text = tip[0] if tip else None
            shape = re.sub(r"\d+", "#", text) if text else None      # live numbers ticking is not a new line
            if shape and shape != prev_tip:
                tips.append((src.gt, text))
            prev_tip = shape
    finally:
        waves_mod.WaveTracker.update, waves_mod.WaveTracker.waves = patched   # type: ignore[method-assign]
    texts = [(t, k, x) for t, k, x in eng.text_messages]
    extras = eng.coach_extras() if hasattr(eng, "coach_extras") else {}
    extras = dict(extras or {})
    extras["genie"] = [(gt, c) for gt, c in getattr(eng, "macro_calls", [])]
    tac = getattr(eng, "_tactics", None)
    extras["genie_cancelled"] = list(getattr(getattr(tac, "macro", None), "cancelled", []) or [])
    extras["badges"] = [p for p in getattr(eng, "fx_pushed", []) if str(getattr(p, "rule", "")).startswith("genie:")]
    return SimResult(minutes, list(voice.said), toasts, tips, texts, extras)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--level", default="intermediaire")
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--hz", type=float, default=4.0)
    ap.add_argument("--quiet", action="store_true", help="rates only")
    a = ap.parse_args(argv)
    import logging
    logging.disable(logging.WARNING)
    res = run(a.level, a.minutes, a.hz)
    out = sys.stdout
    if out is None:
        return 0
    print(f"niveau {a.level}: {res.rates()}  minute la plus chargée: {res.busiest_minute()}", file=out)
    fmt = lambda gt: f"{int(gt // 60):02d}:{int(gt % 60):02d}"     # noqa: E731
    genie = res.extras.get("genie") or []
    kinds: dict[str, int] = {}
    for _gt, c in genie:
        kinds[c.kind] = kinds.get(c.kind, 0) + 1
    print(f"COUPS DE GÉNIE : {len(genie)} appels ({res.genie_rate()} / min), {len(res.extras.get('badges') or [])} "
          f"badges, {len(res.extras.get('genie_cancelled') or [])} annulés ; par type : {kinds}", file=out)
    for gt, c in genie:
        print(f"  GÉNIE {fmt(gt)} [{c.title}] {c.text} | POURQUOI : {c.why} (score {c.score:.2f}"
              f"{', badge' if c.genius else ''})", file=out)
    if not a.quiet:
        for gt, txt in res.voice:
            print(f"  VOIX  {fmt(gt)} {txt}", file=out)
        for gt, title, sub in res.toasts:
            print(f"  TOAST {fmt(gt)} [{title}] {sub}", file=out)
        for gt, txt in res.tips:
            print(f"  HUD   {fmt(gt)} {txt}", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
