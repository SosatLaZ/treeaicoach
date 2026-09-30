"""Scenario tests of treeaicoach.gank (ARCHITECTURE.md §4.13), deterministic clock.

Each scenario feeds identified icons into the REAL :class:`Tracker` at 8 fps, runs the
:class:`GankAnalyzer` and the :class:`AlertThrottler`, and checks the alerts against the true
distances of the scripted champions.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable

import pytest

from treeaicoach.alerts import Alert, AlertKind, AlertThrottler, Level
from treeaicoach.config import Config
from treeaicoach.gank import GankAnalyzer, GankState
from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.tracker import Tracker

try:  # real dataclasses when available (identifier is written in parallel)
    from treeaicoach.detector import Detection
    from treeaicoach.identifier import Identified
except Exception:  # pragma: no cover - light stand-ins, same fields as the contract
    @dataclass
    class Detection:  # type: ignore[no-redef]
        u: float
        v: float
        r: float
        score: float
        cls: str
        cls_probs: tuple[float, float, float]

    @dataclass
    class Identified:  # type: ignore[no-redef]
        det: Detection
        alias: str | None
        relation: str
        team: str | None
        id_score: float


FPS = 8.0
DT = 1.0 / FPS
WARN = 0.22
DANGER = 0.12
ME_TOP = (0.09, 0.22)                    # blue top laner, in the top lane

GANK_KINDS = {AlertKind.JUNGLER_APPROACH, AlertKind.ROAM_APPROACH, AlertKind.COLLAPSE}

_ROSTER = [  # alias, French name, team, position, smite
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
]


def make_game(t: float, game_time0: float = 400.0, positions: bool = True, dead: bool = False,
              map_number: int = 11) -> GameInfo:
    players = [PlayerInfo(riot_id=f"{a}#EUW", summoner_name=a, champion_alias=a, champion_name=n,
                          team=team, position=pos if positions else "", has_smite=smite)
               for a, n, team, pos, smite in _ROSTER]
    me = players[0]
    me.is_dead = dead
    return GameInfo(game_time=game_time0 + t, game_mode="CLASSIC", map_number=map_number,
                    me=me, allies=players[1:5], enemies=players[5:], fetched_at=t)


def ident(u: float, v: float, relation: str, alias: str | None, team: str | None) -> Identified:
    probs = (0.9, 0.05, 0.05) if relation == "enemy" else (0.05, 0.9, 0.05)
    cls = "enemy" if relation == "enemy" else "ally"
    return Identified(det=Detection(u=u, v=v, r=0.047, score=0.9, cls=cls, cls_probs=probs),
                      alias=alias, relation=relation, team=team, id_score=0.8 if alias else 0.0)


Pos = tuple[float, float]
EnemyFn = Callable[[float], dict]       # t -> {alias or "?n": (u, v)} (visible enemies)


@dataclass
class Tick:
    t: float
    raw: list[Alert]
    said: list[Alert]
    true_d: dict[str, float]


def lerp_path(points: list[Pos], speed: float, t: float) -> Pos:
    """Position on a polyline walked at ``speed`` (normalized / s), clamped at the end."""
    left = speed * max(0.0, t)
    for a, b in zip(points, points[1:]):
        seg = math.dist(a, b)
        if left <= seg:
            k = left / seg if seg > 0 else 0.0
            return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)
        left -= seg
    return points[-1]


def simulate(duration: float, enemies: EnemyFn, me: Callable[[float], Pos | None] = lambda t: ME_TOP,
             game: Callable[[float], GameInfo | None] = make_game, cfg: Config | None = None,
             jitter: float = 0.0, drop: float = 0.0, seed: int = 0, t0: float = 0.0) -> list[Tick]:
    rng = random.Random(seed)
    tracker = Tracker()
    analyzer = GankAnalyzer(cfg or Config())
    throttler = AlertThrottler()
    out: list[Tick] = []
    n = int(round(duration * FPS))
    for i in range(n):
        t = t0 + i * DT

        def noisy(p: Pos) -> Pos:
            return (p[0] + rng.uniform(-jitter, jitter), p[1] + rng.uniform(-jitter, jitter))

        items: list[Identified] = []
        my = me(t)
        if my is not None and rng.random() >= drop:
            u, v = noisy(my)
            items.append(ident(u, v, "self", "Garen", "ORDER"))
        visible = enemies(t)
        for name, p in visible.items():
            if rng.random() < drop:
                continue
            u, v = noisy(p)
            alias = None if name.startswith("?") else name
            items.append(ident(u, v, "enemy", alias, "CHAOS" if alias else None))
        tracker.update(t, items)
        g = game(t)
        raw = analyzer.update(t, tracker, g)
        said = throttler.filter(list(raw), t)
        true_d = {k: math.dist(my, p) for k, p in visible.items()} if my is not None else {}
        out.append(Tick(t, raw, said, true_d))
    return out


def raw_of(ticks: list[Tick], kind: AlertKind | None = None) -> list[tuple[Tick, Alert]]:
    return [(tk, a) for tk in ticks for a in tk.raw if kind is None or a.kind == kind]


def said_of(ticks: list[Tick], kinds=None) -> list[tuple[Tick, Alert]]:
    return [(tk, a) for tk in ticks for a in tk.said if kinds is None or a.kind in kinds]


# --------------------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------------------


def darius_wobble(t: float) -> Pos:
    return (0.09 + 0.012 * math.sin(0.7 * t), 0.15 + 0.02 * math.sin(0.45 * t))


def test_lane_opponent_next_to_me_for_60s_no_alert() -> None:
    ticks = simulate(60.0, lambda t: {"Darius": darius_wobble(t)})
    assert min(tk.true_d["Darius"] for tk in ticks) < DANGER     # really close
    assert raw_of(ticks) == []


def test_lane_opponent_without_riot_positions_learned_from_zone_history() -> None:
    ticks = simulate(60.0, lambda t: {"Darius": darius_wobble(t)},
                     game=lambda t: make_game(t, positions=False))
    late = [a for tk, a in raw_of(ticks) if tk.t >= 6.0]
    assert late == []                     # >= 5 s observed in my lane -> lane opponent


def jungler_path_fn(start_delay: float = 0.0) -> EnemyFn:
    path = [(0.45, 0.30), (0.30, 0.30), (0.12, 0.23)]
    return lambda t: {"LeeSin": lerp_path(path, 0.025, t - start_delay)}


@pytest.mark.parametrize("jitter,drop", [(0.0, 0.0), (0.01, 0.2)])
def test_jungler_gank_warning_then_danger(jitter: float, drop: float) -> None:
    ticks = simulate(16.0, jungler_path_fn(), jitter=jitter, drop=drop, seed=3)
    gank = [(tk, a) for tk, a in raw_of(ticks) if a.kind in GANK_KINDS]
    assert gank, "no gank alert"
    # never before entering the warn radius (small tolerance for noise / smoothing)
    for tk, _a in gank:
        assert tk.true_d["LeeSin"] < WARN + 0.015
    kinds = {a.kind for _tk, a in gank}
    assert kinds == {AlertKind.JUNGLER_APPROACH}
    first_warn = next(tk for tk, a in gank if a.level == Level.WARNING)
    first_danger = next(tk for tk, a in gank if a.level == Level.DANGER)
    assert first_warn.t < first_danger.t
    assert WARN - 0.05 < first_warn.true_d["LeeSin"] < WARN + 0.015
    assert DANGER - 0.02 < first_danger.true_d["LeeSin"] < DANGER + 0.015
    # what the voice says: "Attention, Lee Sin approche." then "Gank ! Lee Sin, recule !"
    said = [a for _tk, a in said_of(ticks, GANK_KINDS)]
    assert said[0].level == Level.WARNING and said[0].text == "Attention, Lee Sin approche."
    assert any(a.level == Level.DANGER and a.text == "Gank ! Lee Sin, recule !" for a in said)
    assert all(a.key == "jungler_approach:LeeSin" and a.alias == "LeeSin" for a in said)
    assert len(said) <= 3                 # no flapping (WARNING, DANGER, maybe a DANGER repeat)
    # first sighting after 1:30 at distance >= warn -> jungler spotted, once
    spotted = raw_of(ticks, AlertKind.JUNGLER_SPOTTED)
    assert len(spotted) == 1 and spotted[0][1].level == Level.INFO
    assert spotted[0][1].text == "Jungler ennemi vu dans la jungle ennemie du haut."


@pytest.mark.parametrize("name", ["Ahri", "?1"])
def test_enemy_appearing_from_fog_close_is_danger_immediately(name: str) -> None:
    appear = 2.0
    p = (ME_TOP[0] + 0.08, ME_TOP[1])
    ticks = simulate(4.0, lambda t: {name: p} if t >= appear else {})
    first = next(tk for tk in ticks if tk.t >= appear)
    assert [a.level for a in first.raw] == [Level.DANGER]
    assert first.raw[0].kind == AlertKind.ROAM_APPROACH
    assert first.said and first.said[0].level == Level.DANGER
    if name == "Ahri":
        assert first.said[0].text == "Gank ! Ahri arrive, recule !"
    else:
        assert first.said[0].text == "Gank ! Un ennemi arrive, recule !"
        assert first.said[0].key == "roam_approach:enemy?1" and first.said[0].alias is None
    assert all(tk.raw == [] for tk in ticks if tk.t < appear)


@pytest.mark.parametrize("positions", [True, False])
def test_mid_laner_roaming_top(positions: bool) -> None:
    path = [(0.28, 0.24), (0.12, 0.22)]

    def enemies(t: float) -> dict:
        if t < 30.0:
            return {"Ahri": (0.5 + 0.01 * math.sin(t), 0.5)}          # farming mid
        if t < 40.0:
            return {}                                                 # walks in the fog
        return {"Ahri": lerp_path(path, 0.025, t - 40.0)}

    ticks = simulate(48.0, enemies, game=lambda t: make_game(t, positions=positions))
    roam = raw_of(ticks, AlertKind.ROAM_APPROACH)
    assert roam and all(tk.t >= 40.0 for tk, _a in roam)
    levels = [a.level for _tk, a in roam]
    assert Level.WARNING in levels and Level.DANGER in levels
    said = [a for _tk, a in said_of(ticks, GANK_KINDS)]
    assert said[0].text == "Ahri arrive vers toi."
    assert any(a.text == "Gank ! Ahri arrive, recule !" for a in said)


def test_three_enemies_converging_collapse() -> None:
    paths = {
        "LeeSin": [(0.40, 0.30), (0.12, 0.22)],
        "Ahri": [(0.33, 0.12), (0.11, 0.20)],
        "Darius": [(0.09, 0.02), (0.09, 0.18)],
    }
    ticks = simulate(14.0, lambda t: {k: lerp_path(p, 0.025, t) for k, p in paths.items()})
    collapse = raw_of(ticks, AlertKind.COLLAPSE)
    assert collapse
    assert all(a.level == Level.DANGER and a.key == "collapse" for _tk, a in collapse)
    for tk, _a in collapse:
        assert sum(1 for d in tk.true_d.values() if d < WARN + 0.015) >= 2
    assert any(a.text == "Danger, 3 ennemis arrivent, recule !" for _tk, a in collapse)
    assert said_of(ticks, {AlertKind.COLLAPSE})


def test_lane_opponent_alone_close_is_not_collapse_but_with_roamer_it_is() -> None:
    # Darius next to me + Caitlyn (bot laner) walking in -> collapse
    path = [(0.35, 0.25), (0.11, 0.21)]
    ticks = simulate(14.0, lambda t: {"Darius": darius_wobble(t),
                                      "Caitlyn": lerp_path(path, 0.025, t)})
    assert raw_of(ticks, AlertKind.COLLAPSE)
    assert all(a.alias != "Darius" for _tk, a in raw_of(ticks) if a.kind != AlertKind.COLLAPSE)


@pytest.mark.parametrize("case", ["dead", "base", "unknown", "aram"])
def test_suppressed_when_dead_in_base_unknown_or_other_map(case: str) -> None:
    close = {"LeeSin": (ME_TOP[0] + 0.05, ME_TOP[1])}
    me = (lambda t: ME_TOP)
    game = make_game
    enemies = (lambda t: close)
    if case == "dead":
        game = lambda t: make_game(t, dead=True)            # noqa: E731
    elif case == "base":
        me = (lambda t: (0.06, 0.94))                       # noqa: E731
        enemies = (lambda t: {"LeeSin": (0.10, 0.90)})      # noqa: E731
    elif case == "unknown":
        me = (lambda t: ME_TOP if t < 1.0 else None)        # noqa: E731
        enemies = (lambda t: close if t >= 4.5 else {})     # noqa: E731
    elif case == "aram":
        game = lambda t: make_game(t, map_number=12)        # noqa: E731
    tracker_ticks = simulate(8.0, enemies, me=me, game=game)
    assert raw_of(tracker_ticks) == []


def test_state_snapshot_reports_suppression_and_threat() -> None:
    tracker = Tracker()
    an = GankAnalyzer(Config())
    assert isinstance(an.state(), GankState) and an.state().level == -1
    t = 0.0
    for i in range(10):
        t = i * DT
        tracker.update(t, [ident(*ME_TOP, "self", "Garen", "ORDER"),
                           ident(ME_TOP[0] + 0.05, ME_TOP[1], "enemy", "LeeSin", "CHAOS")])
        an.update(t, tracker, make_game(t))
    st = an.state()
    assert st.level == int(Level.DANGER) and st.jungler_key == "LeeSin" and st.my_lane == "top"
    an.update(t + DT, tracker, make_game(t, dead=True))
    assert an.state().suppressed == "dead"


def test_config_toggles_disable_alerts() -> None:
    cfg = Config(alert_jungler_approach=False, alert_roam=False, alert_collapse=False,
                 alert_jungler_spotted=False)
    paths = {"LeeSin": [(0.40, 0.30), (0.12, 0.22)], "Ahri": [(0.33, 0.12), (0.11, 0.20)]}
    ticks = simulate(12.0, lambda t: {k: lerp_path(p, 0.025, t) for k, p in paths.items()}, cfg=cfg)
    assert raw_of(ticks) == []


def test_sensitivity_scales_radii() -> None:
    p = (ME_TOP[0] + 0.13, ME_TOP[1])          # outside danger 0.12, inside 0.12 * 1.5
    ticks = simulate(3.0, lambda t: {"LeeSin": p}, cfg=Config(sensitivity=1.5))
    assert any(a.level == Level.DANGER for _tk, a in raw_of(ticks))
    ticks = simulate(3.0, lambda t: {"LeeSin": p})
    assert all(a.level < Level.DANGER for _tk, a in raw_of(ticks))


def test_jungler_spotted_bot_side_after_30s_hidden() -> None:
    def enemies(t: float) -> dict:
        if t < 5.0:
            return {"LeeSin": (0.40, 0.25)}
        if t < 35.0:
            return {}
        return {"LeeSin": (0.70 + 0.003 * (t - 35.0), 0.70)}

    ticks = simulate(40.0, enemies)
    spotted = raw_of(ticks, AlertKind.JUNGLER_SPOTTED)
    later = [(tk, a) for tk, a in spotted if tk.t >= 35.0]
    assert len(later) == 1
    tk, a = later[0]
    assert tk.t == pytest.approx(35.0, abs=0.2)
    assert a.level == Level.INFO and a.key == "jungler_spotted:LeeSin"
    assert a.text == "Jungler ennemi vu dans la rivière du bas."
    assert said_of(ticks, {AlertKind.JUNGLER_SPOTTED})[-1][1].text == a.text
    assert raw_of(ticks, AlertKind.JUNGLER_APPROACH) == []


def test_jungler_spotted_labels_my_jungle_and_needs_25s_hidden() -> None:
    def enemies(t: float) -> dict:
        if t < 3.0:
            return {"LeeSin": (0.40, 0.25)}
        if 23.0 <= t < 26.0:
            return {"LeeSin": (0.45, 0.30)}          # back after 20 s: not announced
        if t >= 55.0:
            return {"LeeSin": (0.60, 0.75)}          # blue bot jungle after 29 s
        return {}

    ticks = simulate(58.0, enemies)
    spotted = [(tk.t, a.text) for tk, a in raw_of(ticks, AlertKind.JUNGLER_SPOTTED)]
    assert [s for s in spotted if 20.0 < s[0] < 30.0] == []
    assert [s[1] for s in spotted if s[0] >= 55.0] == ["Jungler ennemi vu dans ta jungle du bas."]


def test_jungler_first_sighting_before_1m30_not_announced() -> None:
    ticks = simulate(3.0, lambda t: {"LeeSin": (0.45, 0.30)},
                     game=lambda t: make_game(t, game_time0=60.0))
    assert raw_of(ticks, AlertKind.JUNGLER_SPOTTED) == []


def test_noisy_stationary_enemies_do_not_flap_or_fake_approach() -> None:
    # Ahri idles at d ~0.17 (inside warn, outside danger), Darius laning, 60 s,
    # jitter +-0.01 on every icon (me included), 20 % dropped detections.
    def enemies(t: float) -> dict:
        return {"Ahri": (ME_TOP[0] + 0.17, ME_TOP[1] + 0.01), "Darius": darius_wobble(t)}

    for seed in range(3):
        ticks = simulate(60.0, enemies, jitter=0.01, drop=0.2, seed=seed)
        late = [(tk.t, a.kind, a.level) for tk, a in raw_of(ticks) if tk.t >= 1.5]
        assert late == [], f"seed {seed}: {late[:5]}"
        said = said_of(ticks)
        assert len(said) <= 1                 # at most the "just appeared" warning


def test_noisy_jungler_walk_no_early_alert_and_no_flapping() -> None:
    for seed in range(3):
        ticks = simulate(16.0, jungler_path_fn(), jitter=0.01, drop=0.2, seed=seed)
        gank = [(tk, a) for tk, a in raw_of(ticks) if a.kind in GANK_KINDS]
        assert gank and all(tk.true_d["LeeSin"] < WARN + 0.015 for tk, _a in gank)
        said = [a for _tk, a in said_of(ticks, GANK_KINDS)]
        assert [a.level for a in said][:2] == [Level.WARNING, Level.DANGER]
        assert len(said) <= 3


def test_unidentified_enemy_approach_triggers_roam() -> None:
    path = [(0.35, 0.25), (0.11, 0.21)]
    ticks = simulate(12.0, lambda t: {"?1": lerp_path(path, 0.025, t)})
    roam = raw_of(ticks, AlertKind.ROAM_APPROACH)
    assert roam and {a.key for _tk, a in roam} == {"roam_approach:enemy?1"}
    said = [a.text for _tk, a in said_of(ticks, GANK_KINDS)]
    assert said[0] == "Un ennemi arrive vers toi." and "Gank ! Un ennemi arrive, recule !" in said


def test_unidentified_lane_opponent_is_learned() -> None:
    ticks = simulate(40.0, lambda t: {"?1": darius_wobble(t)},
                     game=lambda t: make_game(t, positions=False))
    assert [a for tk, a in raw_of(ticks) if tk.t >= 6.0] == []


def test_laner_mia_once_per_disappearance() -> None:
    cfg = Config(alert_laner_mia=True)

    def enemies(t: float) -> dict:
        return {"Darius": darius_wobble(t)} if t < 10.0 or 30.0 <= t < 32.0 else {}

    ticks = simulate(45.0, enemies, cfg=cfg)
    mia = raw_of(ticks, AlertKind.LANER_MIA)
    assert [round(tk.t) for tk, _a in mia] == [16, 38]
    assert all(a.level == Level.INFO and a.text == "Darius a disparu, prudence." for _tk, a in mia)
    # option off (default) or before 3:00 -> nothing
    assert raw_of(simulate(20.0, enemies), AlertKind.LANER_MIA) == []
    early = simulate(20.0, enemies, cfg=cfg, game=lambda t: make_game(t, game_time0=100.0))
    assert raw_of(early, AlertKind.LANER_MIA) == []


def test_no_game_info_still_works_with_roamers() -> None:
    path = [(0.35, 0.25), (0.11, 0.21)]
    ticks = simulate(12.0, lambda t: {"LeeSin": lerp_path(path, 0.025, t)}, game=lambda t: None)
    kinds = {a.kind for _tk, a in raw_of(ticks)}
    assert kinds == {AlertKind.ROAM_APPROACH}           # jungler unknown without the API


def test_never_raises_and_reset() -> None:
    an = GankAnalyzer(Config())
    assert an.update(float("nan"), Tracker(), None) == []
    assert an.update(0.0, None, None) == []                        # type: ignore[arg-type]
    assert an.update(0.0, object(), object()) == []                # type: ignore[arg-type]
    an.apply_config(Config(sensitivity=1.2))
    an.reset()
    assert an.state().t is None
    assert an.is_approaching("LeeSin") is False
