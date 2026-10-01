"""Scenario tests of treeaicoach.gank (calm, role-aware gank alerts), deterministic clock.

Each scenario feeds identified icons into the REAL :class:`Tracker` at 8 fps, runs the
:class:`GankAnalyzer` and the :class:`AlertThrottler`, and checks the alerts against the true
distances of the scripted champions.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Callable

import pytest

from treeaicoach.alerts import Alert, AlertKind, AlertThrottler, Level
from treeaicoach.config import Config
from treeaicoach.gank import JUNGLER_EARLY_FACTOR, GankAnalyzer, GankState
from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.tracker import Tracker

try:  # real dataclasses when available
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
ME_BOT = (0.75, 0.915)                   # blue ADC, in the bot lane

GANK_KINDS = {AlertKind.JUNGLER_APPROACH, AlertKind.ROAM_APPROACH, AlertKind.COLLAPSE}

ROSTER = [  # alias, French name, team, position, smite
    ("Garen", "Garen", "ORDER", "TOP", False),
    ("Vi", "Vi", "ORDER", "JUNGLE", True),
    ("Lux", "Lux", "ORDER", "MIDDLE", False),
    ("Jinx", "Jinx", "ORDER", "BOTTOM", False),
    ("Renata", "Renata Glasc", "ORDER", "UTILITY", False),
    ("Darius", "Darius", "CHAOS", "TOP", False),
    ("LeeSin", "Lee Sin", "CHAOS", "JUNGLE", True),
    ("Ahri", "Ahri", "CHAOS", "MIDDLE", False),
    ("Caitlyn", "Caitlyn", "CHAOS", "BOTTOM", False),
    ("Nautilus", "Nautilus", "CHAOS", "UTILITY", False),
]


def make_game(t: float, game_time0: float = 400.0, positions: bool = True, dead: bool = False,
              map_number: int = 11, me_alias: str = "Garen") -> GameInfo:
    players = [PlayerInfo(riot_id=f"{a}#EUW", summoner_name=a, champion_alias=a, champion_name=n,
                          team=team, position=pos if positions else "", has_smite=smite)
               for a, n, team, pos, smite in ROSTER]
    me = next(p for p in players if p.champion_alias == me_alias)
    me.is_dead = dead
    allies = [p for p in players if p.team == me.team and p is not me]
    enemies = [p for p in players if p.team != me.team]
    return GameInfo(game_time=game_time0 + t, game_mode="CLASSIC", map_number=map_number,
                    me=me, allies=allies, enemies=enemies, fetched_at=t)


def ident(u: float, v: float, relation: str, alias: str | None, team: str | None,
          score: float = 0.9, id_score: float | None = None) -> Identified:
    probs = (0.9, 0.05, 0.05) if relation == "enemy" else (0.05, 0.9, 0.05)
    cls = "enemy" if relation == "enemy" else "ally"
    return Identified(det=Detection(u=u, v=v, r=0.047, score=score, cls=cls, cls_probs=probs),
                      alias=alias, relation=relation, team=team,
                      id_score=(0.8 if alias else 0.0) if id_score is None else id_score)


Pos = tuple[float, float]
IconFn = Callable[[float], dict]   # t -> {alias or "?n": (u, v) | {"pos", "score", "id_score", "relation"}}


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


def simulate(duration: float, enemies: IconFn, me: Callable[[float], Pos | None] = lambda t: ME_TOP,
             game: Callable[[float], GameInfo | None] = make_game, cfg: Config | None = None,
             jitter: float = 0.0, drop: float = 0.0, seed: int = 0, t0: float = 0.0,
             allies: IconFn = lambda t: {}, me_alias: str = "Garen",
             analyzer: GankAnalyzer | None = None) -> list[Tick]:
    rng = random.Random(seed)
    tracker = Tracker()
    analyzer = analyzer or GankAnalyzer(cfg or Config())
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
            items.append(ident(u, v, "self", me_alias, "ORDER"))
        for side, icons in (("ally", allies(t)), ("enemy", enemies(t))):
            for name, spec in icons.items():
                if rng.random() < drop:
                    continue
                spec = spec if isinstance(spec, dict) else {"pos": spec}
                u, v = noisy(spec["pos"])
                alias = None if name.startswith("?") else name
                relation = spec.get("relation", side)
                team = ("CHAOS" if relation == "enemy" else "ORDER") if alias else None
                items.append(ident(u, v, relation, alias, team, score=spec.get("score", 0.9),
                                   id_score=spec.get("id_score")))
        tracker.update(t, items)
        g = game(t)
        raw = analyzer.update(t, tracker, g)
        said = throttler.filter(list(raw), t)
        vis = {k: (s["pos"] if isinstance(s, dict) else s) for k, s in enemies(t).items()}
        true_d = {k: math.dist(my, p) for k, p in vis.items()} if my is not None else {}
        out.append(Tick(t, raw, said, true_d))
    return out


def raw_of(ticks: list[Tick], kind: AlertKind | None = None) -> list[tuple[Tick, Alert]]:
    return [(tk, a) for tk in ticks for a in tk.raw if kind is None or a.kind == kind]


def gank_raw(ticks: list[Tick]) -> list[tuple[Tick, Alert]]:
    return [(tk, a) for tk, a in raw_of(ticks) if a.kind in GANK_KINDS]


def said_of(ticks: list[Tick], kinds=None) -> list[tuple[Tick, Alert]]:
    return [(tk, a) for tk in ticks for a in tk.said if kinds is None or a.kind in kinds]


def said_texts(ticks: list[Tick], kinds=GANK_KINDS) -> list[str]:
    return [a.text for _tk, a in said_of(ticks, kinds)]


def darius_wobble(t: float) -> Pos:
    return (0.09 + 0.012 * math.sin(0.7 * t), 0.15 + 0.02 * math.sin(0.45 * t))


def jungler_path_fn(start_delay: float = 0.0) -> IconFn:
    path = [(0.45, 0.30), (0.30, 0.30), (0.12, 0.23)]
    return lambda t: {"LeeSin": lerp_path(path, 0.025, t - start_delay)}


# --------------------------------------------------------------------------------------
# Lane opponents: laning is not a gank
# --------------------------------------------------------------------------------------


def test_lane_opponent_next_to_me_for_60s_no_alert() -> None:
    ticks = simulate(60.0, lambda t: {"Darius": darius_wobble(t)})
    assert min(tk.true_d["Darius"] for tk in ticks) < DANGER     # really close
    assert raw_of(ticks) == []


def test_lane_opponent_without_riot_positions_known_from_roles() -> None:
    # no Riot positions: roles inferred (Garen / Darius are top laners) -> silent from the start
    ticks = simulate(60.0, lambda t: {"Darius": darius_wobble(t)},
                     game=lambda t: make_game(t, positions=False))
    assert raw_of(ticks) == []


def test_unidentified_lane_opponent_is_learned() -> None:
    ticks = simulate(40.0, lambda t: {"?1": darius_wobble(t)},
                     game=lambda t: make_game(t, positions=False))
    assert gank_raw(ticks) == []          # anonymous: DANGER needs 5 frames, lane history wins


def test_lane_opponent_seen_again_without_identity_is_not_a_gank() -> None:
    # Darius is identified, walks into a bush, and comes back out unidentified next to me
    def enemies(t: float) -> dict:
        if t < 10.0:
            return {"Darius": darius_wobble(t)}
        if t < 13.0:
            return {}
        return {"?1": (0.09, 0.17)}

    ticks = simulate(20.0, enemies)
    assert gank_raw(ticks) == []


def test_bot_lane_2v2_lane_opponents_are_silent() -> None:
    def enemies(t: float) -> dict:
        return {"Caitlyn": (0.84 + 0.02 * math.sin(0.6 * t), 0.905),
                "Nautilus": (0.81 + 0.03 * math.sin(0.4 * t), 0.885 + 0.01 * math.sin(t))}

    ticks = simulate(60.0, enemies, me=lambda t: ME_BOT, me_alias="Jinx",
                     game=lambda t: make_game(t, me_alias="Jinx"),
                     allies=lambda t: {"Renata": (0.73, 0.90)})
    assert min(tk.true_d["Nautilus"] for tk in ticks) < DANGER
    assert raw_of(ticks) == []


# --------------------------------------------------------------------------------------
# Real ganks
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("jitter,drop", [(0.0, 0.0), (0.01, 0.2)])
def test_jungler_gank_warning_then_danger(jitter: float, drop: float) -> None:
    ticks = simulate(16.0, jungler_path_fn(), jitter=jitter, drop=drop, seed=3)
    gank = gank_raw(ticks)
    assert gank, "no gank alert"
    early = WARN * JUNGLER_EARLY_FACTOR    # the jungler clearly coming at me: ~2 s earlier
    for tk, _a in gank:                   # never before entering the (early) warn radius
        assert tk.true_d["LeeSin"] < early + 0.015
    assert {a.kind for _tk, a in gank} == {AlertKind.JUNGLER_APPROACH}
    first_warn = next(tk for tk, a in gank if a.level == Level.WARNING)
    first_danger = next(tk for tk, a in gank if a.level == Level.DANGER)
    assert first_warn.t < first_danger.t
    assert WARN < first_warn.true_d["LeeSin"] < early + 0.015
    # contact lead: WARNING >= 5 s before the enemy reaches the danger radius
    assert first_danger.t - first_warn.t >= 5.0
    assert DANGER - 0.03 < first_danger.true_d["LeeSin"] < DANGER + 0.015
    said = [a for _tk, a in said_of(ticks, GANK_KINDS)]
    # announced earlier (ETA ~9 s): he is still in the enemy jungle, not yet in the river
    assert [a.text for a in said] in (["Lee Sin arrive par la jungle ennemie !", "Gank ! Lee Sin, recule !"],
                                      ["Lee Sin arrive par la rivière !", "Gank ! Lee Sin, recule !"])
    assert all(a.key == "jungler_approach:LeeSin" and a.alias == "LeeSin" for a in said)
    # first sighting after 1:30, far away: announced once (it will not be repeated on the same side)
    spotted = raw_of(ticks, AlertKind.JUNGLER_SPOTTED)
    assert [(tk.t, a.text) for tk, a in spotted] == [(0.0, "Jungler ennemi vu dans la jungle ennemie du haut.")]


@pytest.mark.parametrize("name,frames", [("Ahri", 1), ("?1", 3)])
def test_enemy_appearing_from_fog_close_is_danger_after_confirmation(name: str, frames: int) -> None:
    appear = 2.0
    p = (ME_TOP[0] + 0.08, ME_TOP[1] + 0.03)
    ticks = simulate(4.0, lambda t: {name: p} if t >= appear else {})
    first = next(tk for tk in ticks if tk.raw)
    assert first.t == pytest.approx(appear + (frames - 1) * DT)
    assert [a.level for a in first.raw] == [Level.DANGER]
    assert first.raw[0].kind == AlertKind.ROAM_APPROACH
    assert first.said and first.said[0].level == Level.DANGER
    if name == "Ahri":
        assert first.said[0].text == "Roam ! Ahri, recule !"
    else:
        assert first.said[0].text == "Gank ! Un ennemi arrive, recule !"
        assert first.said[0].key == "roam_approach:enemy?1" and first.said[0].alias is None
    assert len(said_of(ticks)) == 1       # said once (no repeat for 12 s)


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
    assert said_texts(ticks) == ["Roam : Ahri arrive par la jungle ennemie !", "Roam ! Ahri, recule !"]


def test_three_enemies_converging_merged_into_one_sentence() -> None:
    paths = {
        "LeeSin": [(0.40, 0.30), (0.12, 0.22)],
        "Ahri": [(0.33, 0.12), (0.11, 0.20)],
        "Darius": [(0.09, 0.02), (0.09, 0.18)],
    }
    ticks = simulate(14.0, lambda t: {k: lerp_path(p, 0.025, t) for k, p in paths.items()})
    collapse = raw_of(ticks, AlertKind.COLLAPSE)
    assert collapse
    for _tk, a in collapse:
        assert a.members == ("Ahri", "LeeSin")          # the lane opponent is not a ganker
        assert a.text in ("Gank top : Lee Sin et Ahri !", "Gank top : Ahri et Lee Sin !",
                          "Gank top : Lee Sin et Ahri, recule !", "Gank top : Ahri et Lee Sin, recule !")
    # one tick = at most one gank alert
    assert all(sum(a.kind in GANK_KINDS for a in tk.raw) <= 1 for tk in ticks)
    said = said_texts(ticks)
    assert any(s.startswith("Gank top : ") for s in said)
    assert len(said) <= 3                                # calm: no spam
    assert not any("Darius" in s for s in said)


def test_lane_opponent_close_plus_roamer_only_names_the_roamer() -> None:
    path = [(0.35, 0.25), (0.11, 0.21)]
    ticks = simulate(14.0, lambda t: {"Darius": darius_wobble(t),
                                      "Caitlyn": lerp_path(path, 0.025, t)})
    gank = gank_raw(ticks)
    assert gank and all(a.alias == "Caitlyn" and a.members == ("Caitlyn",) for _tk, a in gank)
    assert all("Darius" not in s for s in said_texts(ticks))


def test_bot_lane_2v2_then_jungler_gank() -> None:
    lee = [(0.60, 0.62), (0.70, 0.76), (0.75, 0.88)]

    def enemies(t: float) -> dict:
        out = {"Caitlyn": (0.84 + 0.02 * math.sin(0.6 * t), 0.905),
               "Nautilus": (0.81 + 0.03 * math.sin(0.4 * t), 0.885 + 0.01 * math.sin(t))}
        if t >= 30.0:
            out["LeeSin"] = lerp_path(lee, 0.025, t - 30.0)
        return out

    for positions in (True, False):
        ticks = simulate(44.0, enemies, me=lambda t: ME_BOT, me_alias="Jinx",
                         game=lambda t: make_game(t, me_alias="Jinx", positions=positions),
                         allies=lambda t: {"Renata": (0.73, 0.90)})
        gank = gank_raw(ticks)
        assert gank and all(tk.t >= 30.0 for tk, _a in gank)
        assert {a.alias for _tk, a in gank} == {"LeeSin"}
        assert said_texts(ticks) == ["Lee Sin arrive par la rivière !", "Gank ! Lee Sin, recule !"]


def test_bot_lane_jungler_and_mid_gank_together() -> None:
    # walkable paths down the bot river (gank radii are travel times through the walls)
    lee = [(0.62, 0.60), (0.78, 0.76), (0.76, 0.88)]
    ahri = [(0.64, 0.62), (0.79, 0.775), (0.77, 0.89)]

    def enemies(t: float) -> dict:
        out = {"Caitlyn": (0.84 + 0.02 * math.sin(0.6 * t), 0.905),
               "Nautilus": (0.81 + 0.03 * math.sin(0.4 * t), 0.885)}
        if t >= 10.0:
            out["LeeSin"] = lerp_path(lee, 0.025, t - 10.0)
            out["Ahri"] = lerp_path(ahri, 0.025, t - 10.0)
        return out

    ticks = simulate(24.0, enemies, me=lambda t: ME_BOT, me_alias="Jinx",
                     game=lambda t: make_game(t, me_alias="Jinx"))
    said = said_texts(ticks)
    assert said and said[0] == "Gank bot : Lee Sin et Ahri !"
    assert said[1:] in ([], ["Gank bot : Lee Sin et Ahri, recule !"])


# --------------------------------------------------------------------------------------
# Trust: allies, doubtful identities, noise
# --------------------------------------------------------------------------------------


def test_misidentified_ally_is_never_announced() -> None:
    # an icon "identified" as Renata (MY support) but tagged enemy walks right onto me
    path = [(0.35, 0.25), (0.11, 0.21)]
    ticks = simulate(14.0, lambda t: {"Renata": lerp_path(path, 0.025, t)})
    assert min(tk.true_d["Renata"] for tk in ticks) < DANGER
    assert raw_of(ticks) == []
    assert all("Renata" not in s for s in said_texts(ticks, None))


def test_ally_icons_never_alert() -> None:
    path = [(0.35, 0.25), (0.11, 0.21)]
    ticks = simulate(14.0, lambda t: {}, allies=lambda t: {"Vi": lerp_path(path, 0.025, t),
                                                           "?1": lerp_path(path, 0.02, t)})
    assert raw_of(ticks) == []


def test_doubtful_identity_is_anonymous_danger_only() -> None:
    path = [(0.35, 0.25), (0.11, 0.21)]
    ticks = simulate(14.0, lambda t: {"LeeSin": {"pos": lerp_path(path, 0.025, t), "id_score": 0.45}})
    gank = gank_raw(ticks)
    assert gank and all(a.level == Level.DANGER and a.alias is None for _tk, a in gank)
    assert said_texts(ticks) == ["Gank ! Un ennemi arrive, recule !"]


def test_low_detection_confidence_never_confirms() -> None:
    path = [(0.35, 0.25), (0.11, 0.21)]
    ticks = simulate(14.0, lambda t: {"LeeSin": {"pos": lerp_path(path, 0.025, t), "score": 0.3}})
    assert gank_raw(ticks) == []


def test_single_frame_false_detections_do_not_alert() -> None:
    rng = random.Random(5)
    blips = {round(i * DT, 3): (ME_TOP[0] + rng.uniform(-0.1, 0.1), ME_TOP[1] + rng.uniform(-0.1, 0.1))
             for i in range(0, 480, 7)}

    def enemies(t: float) -> dict:
        p = blips.get(round(t, 3))
        return {"?1": p} if p is not None else {}

    ticks = simulate(60.0, enemies)
    assert gank_raw(ticks) == []


def test_noisy_stationary_enemies_do_not_flap_or_fake_approach() -> None:
    # Ahri idles at d ~0.17 (inside warn, outside danger), Darius laning, 60 s,
    # jitter +-0.01 on every icon (me included), 20 % dropped detections.
    def enemies(t: float) -> dict:
        return {"Ahri": (ME_TOP[0] + 0.17, ME_TOP[1] + 0.01), "Darius": darius_wobble(t)}

    for seed in range(3):
        ticks = simulate(60.0, enemies, jitter=0.01, drop=0.2, seed=seed)
        assert gank_raw(ticks) == [], seed


def test_noisy_jungler_walk_no_early_alert_and_no_flapping() -> None:
    for seed in range(3):
        ticks = simulate(16.0, jungler_path_fn(), jitter=0.01, drop=0.2, seed=seed)
        gank = gank_raw(ticks)
        assert gank and all(tk.true_d["LeeSin"] < WARN * JUNGLER_EARLY_FACTOR + 0.015 for tk, _a in gank)
        said = [a for _tk, a in said_of(ticks, GANK_KINDS)]
        assert [a.level for a in said] in ([Level.WARNING, Level.DANGER], [Level.DANGER])


def test_unidentified_enemy_approach_is_danger_only() -> None:
    path = [(0.35, 0.25), (0.11, 0.21)]
    ticks = simulate(12.0, lambda t: {"?1": lerp_path(path, 0.025, t)})
    roam = raw_of(ticks, AlertKind.ROAM_APPROACH)
    assert roam and {a.key for _tk, a in roam} == {"roam_approach:enemy?1"}
    assert all(a.level == Level.DANGER for _tk, a in roam)
    assert said_texts(ticks) == ["Gank ! Un ennemi arrive, recule !"]


# --------------------------------------------------------------------------------------
# Suppression, options, safe mode
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["dead", "base", "unknown", "aram", "safe"])
def test_suppressed_cases(case: str) -> None:
    close = {"LeeSin": (ME_TOP[0] + 0.05, ME_TOP[1])}
    me = (lambda t: ME_TOP)
    game = make_game
    enemies = (lambda t: close)
    cfg = None
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
    elif case == "safe":
        cfg = Config(safe_mode=True)
    ticks = simulate(8.0, enemies, me=me, game=game, cfg=cfg)
    assert raw_of(ticks) == []


def test_safe_mode_silences_everything_including_spotted_and_mia() -> None:
    cfg = Config(safe_mode=True, alert_laner_mia=True)

    def enemies(t: float) -> dict:
        out = {"Darius": darius_wobble(t)} if t < 10.0 else {}
        out.update(jungler_path_fn(5.0)(t) if t >= 5.0 else {})
        return out

    an = GankAnalyzer(cfg)
    ticks = simulate(40.0, enemies, cfg=cfg, analyzer=an)
    assert raw_of(ticks) == []
    assert an.state().suppressed == "safe_mode"
    assert an.roles()["Garen"].role == "TOP"         # roles stay available for the UI


def test_state_snapshot_reports_suppression_threat_and_roles() -> None:
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
    assert st.my_role == "TOP" and st.threats == frozenset({"LeeSin"})
    assert ("Nautilus", "enemy", "UTILITY") in st.roles and ("Garen", "ally", "TOP") in st.roles
    an.update(t + DT, tracker, make_game(t, dead=True))
    assert an.state().suppressed == "dead"


def test_config_toggles_disable_alerts() -> None:
    cfg = Config(alert_jungler_approach=False, alert_roam=False, alert_collapse=False,
                 alert_jungler_spotted=False)
    paths = {"LeeSin": [(0.40, 0.30), (0.12, 0.22)], "Ahri": [(0.33, 0.12), (0.11, 0.20)]}
    ticks = simulate(12.0, lambda t: {k: lerp_path(p, 0.025, t) for k, p in paths.items()}, cfg=cfg)
    assert raw_of(ticks) == []


def test_collapse_disabled_gives_separate_alerts() -> None:
    paths = {"LeeSin": [(0.40, 0.30), (0.12, 0.22)], "Ahri": [(0.33, 0.12), (0.11, 0.20)]}
    ticks = simulate(12.0, lambda t: {k: lerp_path(p, 0.025, t) for k, p in paths.items()},
                     cfg=Config(alert_collapse=False))
    kinds = {a.kind for _tk, a in gank_raw(ticks)}
    assert kinds == {AlertKind.JUNGLER_APPROACH, AlertKind.ROAM_APPROACH}


def test_sensitivity_scales_radii() -> None:
    p = (ME_TOP[0] + 0.13, ME_TOP[1])          # outside danger 0.12, inside 0.12 * 1.5
    ticks = simulate(3.0, lambda t: {"LeeSin": p}, cfg=Config(sensitivity=1.5))
    assert any(a.level == Level.DANGER for _tk, a in raw_of(ticks))
    ticks = simulate(3.0, lambda t: {"LeeSin": p})
    assert gank_raw(ticks) == []               # standing still, outside danger: calm


# --------------------------------------------------------------------------------------
# Jungler spotted: only when it changes something
# --------------------------------------------------------------------------------------


def test_jungler_spotted_only_on_side_change_and_every_45s() -> None:
    def enemies(t: float) -> dict:
        if t < 5.0:
            return {"LeeSin": (0.40, 0.25)}                  # top side (first sighting: said)
        if 35.0 <= t < 38.0:
            return {"LeeSin": (0.70, 0.70)}                  # bot side, but only 35 s later
        if 70.0 <= t < 73.0:
            return {"LeeSin": (0.72, 0.70)}                  # bot side, same as last seen
        if 110.0 <= t:
            return {"LeeSin": (0.30, 0.35)}                  # top side, > 45 s -> said
        return {}

    ticks = simulate(115.0, enemies)
    spotted = [(round(tk.t), a.text) for tk, a in raw_of(ticks, AlertKind.JUNGLER_SPOTTED)]
    assert spotted == [(0, "Jungler ennemi vu dans la jungle ennemie du haut."),
                       (110, "Jungler ennemi vu dans la rivière du haut.")]
    assert said_texts(ticks, {AlertKind.JUNGLER_SPOTTED}) == [s for _t, s in spotted]
    assert gank_raw(ticks) == []


def test_jungler_first_sighting_before_1m30_not_announced() -> None:
    ticks = simulate(3.0, lambda t: {"LeeSin": (0.45, 0.30)},
                     game=lambda t: make_game(t, game_time0=60.0))
    assert raw_of(ticks, AlertKind.JUNGLER_SPOTTED) == []


def test_jungler_back_after_20s_not_announced() -> None:
    def enemies(t: float) -> dict:
        if t < 3.0:
            return {"LeeSin": (0.40, 0.25)}
        if 23.0 <= t < 26.0:
            return {"LeeSin": (0.70, 0.70)}          # other side but hidden only 20 s
        return {}

    ticks = simulate(30.0, enemies, game=lambda t: make_game(t, game_time0=60.0))
    assert raw_of(ticks, AlertKind.JUNGLER_SPOTTED) == []


# --------------------------------------------------------------------------------------
# Misc
# --------------------------------------------------------------------------------------


def test_laner_mia_once_per_disappearance() -> None:
    cfg = Config(alert_laner_mia=True)

    def enemies(t: float) -> dict:
        # (he vanishes >= 1.2 icon radii away from my icon: not a stacked icon, a real disappearance)
        return {"Darius": darius_wobble(t)} if t < 10.0 or 30.0 <= t < 34.0 else {}

    ticks = simulate(45.0, enemies, cfg=cfg)
    mia = raw_of(ticks, AlertKind.LANER_MIA)
    assert [round(tk.t) for tk, _a in mia] == [16, 40]
    assert all(a.level == Level.INFO and a.text == "Darius a disparu, prudence." for _tk, a in mia)
    assert raw_of(simulate(20.0, enemies), AlertKind.LANER_MIA) == []
    early = simulate(20.0, enemies, cfg=cfg, game=lambda t: make_game(t, game_time0=100.0))
    assert raw_of(early, AlertKind.LANER_MIA) == []


def test_dead_enemy_is_never_missing_nor_a_threat() -> None:
    cfg = Config(alert_laner_mia=True)

    def enemies(t: float) -> dict:
        return {"Darius": darius_wobble(t)} if t < 10.0 else {}

    def game(t: float) -> GameInfo:
        g = make_game(t)
        for p in g.enemies:
            if p.champion_alias == "Darius" and t >= 10.0:
                p.is_dead, p.respawn_timer = True, 30.0
        return g

    assert raw_of(simulate(25.0, enemies, cfg=cfg, game=game), AlertKind.LANER_MIA) == []
    # an enemy the API says is dead never makes a gank alert (stale / false icon)
    path = [(0.35, 0.25), (0.11, 0.21)]

    def dead_lee(t: float) -> GameInfo:
        g = make_game(t)
        for p in g.enemies:
            if p.champion_alias == "LeeSin":
                p.is_dead, p.respawn_timer = True, 20.0
        return g

    ticks = simulate(12.0, lambda t: {"LeeSin": lerp_path(path, 0.025, t)}, game=dead_lee)
    assert gank_raw(ticks) == []
    assert gank_raw(simulate(12.0, lambda t: {"LeeSin": lerp_path(path, 0.025, t)}))


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
    bad: Any = type("Bad", (), {"__getattr__": lambda self, k: 1 / 0})()
    assert an.update(1.0, Tracker(), bad) == []
    an.apply_config(Config(sensitivity=1.2))
    an.reset()
    assert an.state().t is None
    assert an.is_approaching("LeeSin") is False


# --------------------------------------------------------------------------------------
# Real game report (Garen 1/15/5): siege of my base, roams of the mid laner
# --------------------------------------------------------------------------------------


def test_siege_of_my_base_is_still_announced() -> None:
    """33-35 min of the real game: 3 deaths in my base with the enemy jungler, no alert (the
    analyser was silent in the whole base). Only the fountain is silent now."""
    me_base = (0.16, 0.84)                            # my base, outside the fountain
    path = [(0.40, 0.70), (0.18, 0.82)]
    ticks = simulate(12.0, lambda t: {"LeeSin": lerp_path(path, 0.025, t)}, me=lambda t: me_base)
    levels = [a.level for _tk, a in gank_raw(ticks)]
    assert Level.WARNING in levels and Level.DANGER in levels
    said = said_texts(ticks)
    assert said and said[-1] == "Gank ! Lee Sin, recule !"


def test_mid_laner_farming_his_lane_is_not_a_roam() -> None:
    """Ekko farming mid while I walk through the mid lane: not "Roam ! Ekko" (8 roam alerts of the
    real game were mostly unverifiable / false)."""
    me_mid = (0.45, 0.55)
    ticks = simulate(30.0, lambda t: {"Ahri": (0.52 + 0.02 * math.sin(t), 0.48)}, me=lambda t: me_mid)
    assert min(tk.true_d["Ahri"] for tk in ticks) < DANGER
    assert raw_of(ticks, AlertKind.ROAM_APPROACH) == []


def test_roamer_standing_still_near_me_is_not_a_gank_danger() -> None:
    """A roamer already standing near me (warded bush, no approach): no repeated "Roam !" DANGER."""
    still = (ME_TOP[0] + 0.09, ME_TOP[1] + 0.01)      # inside the danger radius, not too close
    ticks = simulate(20.0, lambda t: {"Ahri": still}, t0=0.0)
    roam = [a for _tk, a in raw_of(ticks, AlertKind.ROAM_APPROACH) if a.level == Level.DANGER]
    assert len(said_of(ticks, GANK_KINDS)) <= 1         # at most once (popped at the start), never repeated
    assert len(roam) <= 2


def test_roam_is_labelled_and_announced_once_per_roam() -> None:
    path = [(0.30, 0.24), (0.12, 0.22)]
    ticks = simulate(24.0, lambda t: {"Ahri": lerp_path(path, 0.025, t)})   # then stays on me
    said = said_texts(ticks)
    assert said and all(x.startswith("Roam") for x in said)
    assert said.count("Roam ! Ahri, recule !") == 1
