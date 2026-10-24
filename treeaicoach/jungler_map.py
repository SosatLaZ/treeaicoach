"""Enemy jungler PROBABILITY MAP: where is he, by map part, all game long (BACKLOG 4).

The fog region (:mod:`treeaicoach.fog_tracker`) stops after ~70 s and the logical paths
(:mod:`treeaicoach.jungle_graph`) only live during one disappearance. This model never stops:
it is a per-game Bayesian mixture over the map, rebuilt from every visible fact.

* **Facts** (all public, the same ones fog_tracker ingests): every minimap sighting of the
  jungler, kills he takes part in (the victim's place), epic monsters he takes (the pit), his
  death -> respawn at his fountain (respawn timer), a purchase (Tab: he is in his fountain), a
  creep-score rise (Tab: particles busy on a camp are favoured). The game start is a fact too
  (his fountain at 0:00).
* **Walk model** after the last fact: the particle filter of :mod:`treeaicoach.jungle_graph`
  (camps with respawns, scuttles, objectives, ganks of our visible laners, recalls; his walking
  speed 360 -> 400 units / s), down-weighted where our champions would see him.
* **Prior** of where junglers are by minute, measured on the LCU timelines of the user's past
  games (``tools/jungler_map_truth.py`` builds :data:`ZONE_PRIOR`; per-minute share of the six
  map parts, in the frame of HIS team). The walk model forgets: the older the last fact, the more
  the prior weighs (``1 - exp(-elapsed / TAU_S)``).

Output (:class:`JunglerMapState`): P(top side / mid / bot side / my jungle / his jungle / bases),
P(top half / bottom half), P(he can be on me within ``NEAR_S`` s), a coarse ``GRID x GRID``
probability grid (side shading of the overlay). Validated per minute against the LCU truth
positions (``tools/jungler_map_truth.py``, numbers in :data:`VALIDATION`).

Cost: the filter steps once per game second; the state is rebuilt at most once per game second
(32 x 32 grid, ~0.2 ms); every other tick returns the cached state. numpy only, no thread.
Never raises from the public methods.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from treeaicoach import geometry

log = logging.getLogger(__name__)

#: Output map parts (keys of :attr:`JunglerMapState.zones`).
ZONE_KEYS = ("top", "mid", "bot", "my_jungle", "his_jungle", "base")
#: Finer parts of the prior (jungles split in halves: a jungler's side matters).
FINE_KEYS = ("top", "mid", "bot", "my_jungle_top", "my_jungle_bot", "his_jungle_top", "his_jungle_bot", "base")
ZONE_FR = {"top": "côté haut", "mid": "au mid", "bot": "côté bas", "my_jungle": "dans ta jungle",
           "his_jungle": "dans sa jungle", "base": "à sa base"}
#: Probability grid (cells per side) used for every output.
GRID = 32
#: The walk model fades into the prior with this time constant (s since the last fact).
TAU_S = 30.0
#: Share of the prior even right after a fact (the walk model is never certain).
PRIOR_FLOOR = 0.15
#: A sighting is trusted this much (the rest: prior), identity errors exist.
SEEN_TRUST = 0.95
#: "Near me" horizon (s) and the straight-line -> path factor.
NEAR_S = 20.0
PATH_FACTOR = 1.2
FLASH = 0.027
#: Essentials text: shown from SIDE_SHOW_P, hidden under SIDE_HIDE_P, its value changes at most every
#: SIDE_HOLD_S (unless the half flips) - measured on the 20 real games: 5.8 -> 1.7 value changes / minute shown.
SIDE_SHOW_P = 0.6
SIDE_HIDE_P = 0.6
SIDE_HOLD_S = 15.0
#: Rebuild the outputs at most this often (game seconds).
EVERY_S = 1.0

#: Share of time in each map part per game minute, frame of HIS team (``top`` = the top half
#: when he is CHAOS; rotated for an ORDER jungler). Built by tools/jungler_map_truth.py from the
#: LCU timelines of 20 real games (aggregated counts only), Dirichlet-smoothed, minutes >= the
#: last row reuse it. Order: FINE_KEYS.
ZONE_PRIOR: tuple[tuple[float, ...], ...] = (
    (0.005, 0.003, 0.006, 0.002, 0.071, 0.122, 0.306, 0.485),
    (0.02, 0.002, 0.02, 0.001, 0.083, 0.151, 0.378, 0.344),
    (0.054, 0.002, 0.021, 0.018, 0.119, 0.273, 0.491, 0.022),
    (0.095, 0.003, 0.041, 0.038, 0.075, 0.279, 0.408, 0.061),
    (0.131, 0.039, 0.041, 0.038, 0.039, 0.279, 0.354, 0.079),
    (0.15, 0.039, 0.132, 0.02, 0.003, 0.225, 0.336, 0.097),
    (0.113, 0.039, 0.223, 0.001, 0.021, 0.207, 0.317, 0.079),
    (0.059, 0.021, 0.259, 0.02, 0.039, 0.279, 0.263, 0.061),
    (0.095, 0.057, 0.223, 0.038, 0.039, 0.297, 0.208, 0.042),
    (0.113, 0.112, 0.132, 0.056, 0.039, 0.316, 0.19, 0.042),
    (0.155, 0.097, 0.137, 0.039, 0.022, 0.214, 0.216, 0.12),
    (0.124, 0.083, 0.205, 0.022, 0.023, 0.167, 0.189, 0.187),
    (0.168, 0.023, 0.23, 0.002, 0.023, 0.13, 0.193, 0.231),
    (0.15, 0.024, 0.297, 0.022, 0.024, 0.153, 0.155, 0.174),
    (0.192, 0.003, 0.235, 0.022, 0.024, 0.174, 0.197, 0.153),
    (0.192, 0.045, 0.214, 0.085, 0.045, 0.153, 0.155, 0.111),
    (0.218, 0.046, 0.112, 0.065, 0.131, 0.135, 0.159, 0.135),
    (0.135, 0.047, 0.093, 0.089, 0.177, 0.095, 0.184, 0.181),
    (0.116, 0.07, 0.117, 0.046, 0.159, 0.141, 0.188, 0.163),
    (0.096, 0.162, 0.097, 0.047, 0.117, 0.167, 0.147, 0.167),
    (0.098, 0.19, 0.076, 0.095, 0.12, 0.171, 0.104, 0.147),
    (0.081, 0.204, 0.081, 0.077, 0.104, 0.134, 0.161, 0.158),
    (0.067, 0.156, 0.099, 0.093, 0.065, 0.102, 0.256, 0.162),
    (0.174, 0.138, 0.175, 0.003, 0.005, 0.145, 0.282, 0.078),
    (0.193, 0.154, 0.195, 0.003, 0.005, 0.161, 0.239, 0.049),
    (0.201, 0.102, 0.234, 0.002, 0.037, 0.141, 0.208, 0.075),
    (0.115, 0.148, 0.152, 0.003, 0.148, 0.156, 0.195, 0.083),
    (0.124, 0.121, 0.125, 0.041, 0.198, 0.129, 0.171, 0.09),
    (0.063, 0.16, 0.044, 0.06, 0.16, 0.125, 0.087, 0.301),
    (0.051, 0.162, 0.051, 0.07, 0.117, 0.099, 0.101, 0.349),
)
#: Validation numbers (tools/jungler_map_truth.py), kept for the docs / tests.
VALIDATION: dict = {
    "games": 20, "minutes_hidden": 329,
    "fog": {"zone_ll": 2.004, "zone_acc": 0.419, "half_ll": 0.774, "half_acc": 0.69, "near_brier": 0.233},
    "map": {"zone_ll": 1.288, "zone_acc": 0.556, "half_ll": 0.57, "half_acc": 0.69, "near_brier": 0.197},
    # essentials text shown (steadied, >= 60 %): minutes, share on the right half
    "shown_60": {"fog": (192, 0.766), "map": (184, 0.815)},
    "near_20s": {"fog_ll": 0.903, "map_ll": 0.572},
    # danger.py "X peut arriver" fed by P(near 7 s) instead of the fog mass: never more of my deaths
    # warned (fog 4 / 64, map 1-3 / 64) -> NOT adopted (danger.py unchanged)
    "danger": {"fog": {"alerts": 65, "confirmed": 18, "false": 6, "warned": 4},
               "map_p45": {"alerts": 26, "confirmed": 9, "false": 0, "warned": 2}},
}

_ROT = {"top": "bot", "bot": "top", "my_jungle_top": "my_jungle_bot", "my_jungle_bot": "my_jungle_top",
        "his_jungle_top": "his_jungle_bot", "his_jungle_bot": "his_jungle_top"}


@dataclass(frozen=True)
class JunglerMapState:
    """Snapshot of the map (do not mutate)."""

    gt: float
    zones: dict = field(default_factory=dict)      # ZONE_KEYS -> probability (sums to 1)
    top_half: float = 0.5                          # P(u + v < 1)
    p_near: float | None = None                    # P(he can be on me within NEAR_S s)
    visible: bool = False
    dead: bool = False
    since_fact: float | None = None                # s since the last fact (None: no fact yet)
    grid: np.ndarray | None = None                 # float32 [GRID, GRID], sums to 1 (read-only)
    side_shown: str | None = None                  # steadied "70 % bas" (essentials strip)

    def best(self) -> tuple[str, float] | None:
        if not self.zones:
            return None
        k = max(self.zones, key=lambda z: self.zones[z])
        return k, float(self.zones[k])

    def side_text(self) -> str | None:
        """'70 % bas' / '60 % haut' (the likelier half, steadied: :data:`SIDE_HOLD_S`), or None
        when it is a coin flip / he is visible or dead."""
        return self.side_shown


def _side_value(top: float) -> tuple[str, int]:
    p = max(top, 1.0 - top)
    return ("haut" if top >= 0.5 else "bas"), int(round(p * 10.0)) * 10


# ---------------------------------------------------------------------------------- grids
_STATIC: dict = {}


def _static() -> dict:
    """Zone codes / walkable share / cell centres on the GRID (computed once)."""
    if _STATIC:
        return _STATIC
    g = GRID
    codes = geometry.zone_map(g)
    try:
        from treeaicoach.fog_tracker import shared_reachability

        w = np.asarray(shared_reachability().walkable, np.float32)
        k = w.shape[0] // g
        walk = w[:k * g, :k * g].reshape(g, k, g, k).mean(axis=(1, 3)) if k >= 1 else np.ones((g, g), np.float32)
    except Exception:
        walk = np.ones((g, g), np.float32)
    c = (np.arange(g, dtype=np.float32) + 0.5) / g
    uu, vv = np.meshgrid(c, c)
    _STATIC.update(codes=codes, walk=walk.astype(np.float32), uu=uu, vv=vv,
                   top=(uu + vv < 1.0).astype(np.float32))
    return _STATIC


def fine_masks(my_team: str | None) -> dict[str, np.ndarray]:
    """FINE_KEYS -> bool [GRID, GRID] (my / his jungle from ``my_team``; ORDER when unknown)."""
    s = _static()
    Z = geometry.Zone
    idx = {z: i for i, z in enumerate(geometry.ZONES)}
    codes = s["codes"]
    mine = geometry.normalize_team(my_team) or "ORDER"

    def m(*zs: Any) -> np.ndarray:
        return np.isin(codes, [idx[z] for z in zs])

    blue = {"top": m(Z.BLUE_JUNGLE_TOP), "bot": m(Z.BLUE_JUNGLE_BOT)}
    red = {"top": m(Z.RED_JUNGLE_TOP), "bot": m(Z.RED_JUNGLE_BOT)}
    my, his = (blue, red) if mine == "ORDER" else (red, blue)
    return {"top": m(Z.TOP_LANE, Z.TOP_RIVER), "mid": m(Z.MID_LANE), "bot": m(Z.BOT_LANE, Z.BOT_RIVER),
            "my_jungle_top": my["top"], "my_jungle_bot": my["bot"], "his_jungle_top": his["top"],
            "his_jungle_bot": his["bot"], "base": m(Z.BLUE_BASE, Z.RED_BASE)}


def coarse(fine: str) -> str:
    return fine.rsplit("_", 1)[0] if fine.endswith(("jungle_top", "jungle_bot")) else fine


def zone_masks(my_team: str | None) -> dict[str, np.ndarray]:
    """ZONE_KEYS -> bool [GRID, GRID]."""
    out = {k: None for k in ZONE_KEYS}
    for k, m in fine_masks(my_team).items():
        c = coarse(k)
        out[c] = m if out[c] is None else (out[c] | m)
    return out


def fine_zone_of(uv: Sequence[float], my_team: str | None) -> str:
    """Fine map part of a point (FINE_KEYS)."""
    z = geometry.classify_zone(float(uv[0]), float(uv[1]))
    Z = geometry.Zone
    if z in (Z.TOP_LANE, Z.TOP_RIVER):
        return "top"
    if z == Z.MID_LANE:
        return "mid"
    if z in (Z.BOT_LANE, Z.BOT_RIVER):
        return "bot"
    if z in (Z.BLUE_BASE, Z.RED_BASE):
        return "base"
    owner = geometry.zone_owner(z)
    mine = geometry.normalize_team(my_team) or "ORDER"
    half = "top" if z in (Z.BLUE_JUNGLE_TOP, Z.RED_JUNGLE_TOP) else "bot"
    return ("my_jungle_" if owner == mine else "his_jungle_") + half


def zone_of(uv: Sequence[float], my_team: str | None) -> str:
    """Map part of a point (ZONE_KEYS)."""
    return coarse(fine_zone_of(uv, my_team))


def canonical(zone: str, his_team: str | None) -> str:
    """Zone in the frame where HIS team is CHAOS (top <-> bot for an ORDER jungler)."""
    return _ROT.get(zone, zone) if geometry.normalize_team(his_team) == "ORDER" else zone


def prior_zones(gt: float, his_team: str | None, table: Sequence[Sequence[float]] | None = None
                ) -> dict[str, float]:
    """Prior P(zone) at game time ``gt`` (uniform-ish when no table)."""
    tab = ZONE_PRIOR if table is None else table
    if not tab:
        return {k: 1.0 / len(FINE_KEYS) for k in FINE_KEYS}
    row = tab[max(0, min(len(tab) - 1, int(gt // 60.0)))]
    out = {}
    for k, p in zip(FINE_KEYS, row):
        out[canonical(k, his_team)] = float(p)       # canonical <-> real is an involution
    s = sum(out.values()) or 1.0
    return {k: out.get(k, 0.0) / s for k in FINE_KEYS}


# ---------------------------------------------------------------------------------- model
class JunglerMap:
    """Per-game model of the enemy jungler. Feed facts, call :meth:`step`, read :meth:`state`."""

    def __init__(self, prior: Sequence[Sequence[float]] | None = None, tau_s: float = TAU_S,
                 use_filter: bool = True, n_particles: int | None = None,
                 max_fact_s: float | None = None, start_fact: bool = True) -> None:
        self.prior = prior
        #: (tools) the walk model is dropped this long after the last fact / no game-start fact:
        #: the behaviour of the fog_tracker estimate, the baseline of tools/jungler_map_truth.py
        self.max_fact_s = max_fact_s
        self.start_fact = bool(start_fact)
        self.prior_floor = PRIOR_FLOOR
        self.tau_s = float(tau_s)
        self.use_filter = bool(use_filter)
        self.n_particles = n_particles
        self.reset()

    def reset(self, my_team: str | None = None) -> None:
        self.my_team = geometry.normalize_team(my_team)
        self.his_team = {"ORDER": "CHAOS", "CHAOS": "ORDER"}.get(self.my_team or "")
        self._masks = zone_masks(self.my_team)
        self._fine = fine_masks(self.my_team)
        self._fact: tuple[float, tuple[float, float], str] | None = None
        self._seen_gt: float | None = None
        self._dead_until: float | None = None
        self._pf: Any = None
        self._pf_key: Any = None
        self._gt = 0.0
        self._me: tuple[float, float] | None = None
        self._viewers: list[tuple[float, float]] = []
        self._state = JunglerMapState(gt=0.0)
        self._state_gt = -1e9
        self._farm = False
        self._shown: tuple[str, int, float] | None = None     # (half, value, game time shown)

    def set_team(self, my_team: Any) -> None:
        t = geometry.normalize_team(my_team)
        if t is not None and t != self.my_team:
            fact, seen = self._fact, self._seen_gt
            self.reset(t)
            self._fact, self._seen_gt = fact, seen

    # ------------------------------------------------------------------ facts
    def seen(self, gt: float, uv: Sequence[float]) -> None:
        """The jungler is on the minimap at ``uv`` now."""
        try:
            u, v = geometry.clamp_uv(uv[0], uv[1])
            self._fact = (float(gt), (u, v), "seen")
            self._seen_gt = float(gt)
            self._dead_until = None
        except Exception:
            log.debug("seen failed", exc_info=True)

    def anchor(self, gt: float, uv: Sequence[float], reason: str = "") -> None:
        """A visible fact puts him at ``uv`` at game time ``gt`` (kill, pit, purchase, respawn)."""
        try:
            if self._fact is not None and gt < self._fact[0] - 1e-6:
                return
            u, v = geometry.clamp_uv(uv[0], uv[1])
            self._fact = (float(gt), (u, v), str(reason))
            if reason != "respawn":
                self._dead_until = None
        except Exception:
            log.debug("anchor failed", exc_info=True)

    def dead(self, gt: float, respawn_gt: float | None = None) -> None:
        """He died; he will respawn in his fountain at ``respawn_gt``."""
        rg = float(respawn_gt) if respawn_gt is not None else float(gt) + 30.0
        self._dead_until = rg
        if self.his_team:
            f = geometry.RED_FOUNTAIN if self.his_team == "CHAOS" else geometry.BLUE_FOUNTAIN
            self._fact = (rg, f, "respawn")

    def farm(self) -> None:
        """His creep score went up (Tab)."""
        self._farm = True

    # ------------------------------------------------------------------ walk model
    def _filter(self, gt: float) -> Any:
        if not self.use_filter or self._fact is None:
            return None
        key = (round(self._fact[0], 2), self._fact[1])
        if self._pf is None or self._pf_key != key:
            from treeaicoach.jungle_graph import JunglerFilter, N_PARTICLES

            up, since = self._pf.memory() if self._pf is not None else (None, None)
            if since is not None:
                since += max(0.0, self._fact[0] - self._pf.gt)
            if self._fact[2] in ("respawn", "recall", "start"):
                since = 0.0
            pf = JunglerFilter(n=self.n_particles or N_PARTICLES)
            pf.reset(self._fact[1], self._fact[0], self.his_team, key=key, camps_up=up, since_recall=since)
            self._pf, self._pf_key = pf, key
        return self._pf

    def step(self, gt: float, me: Sequence[float] | None = None,
             viewers: Sequence[Sequence[float]] = ()) -> JunglerMapState:
        """Advance to game time ``gt`` (``me`` / ``viewers``: our visible champions)."""
        try:
            gt = float(gt)
            if not math.isfinite(gt):
                return self._state
            if self._fact is None and gt < 90.0 and self.his_team and self.start_fact:
                f = geometry.RED_FOUNTAIN if self.his_team == "CHAOS" else geometry.BLUE_FOUNTAIN
                self._fact = (0.0, f, "start")
            self._gt = gt
            self._me = (float(me[0]), float(me[1])) if me is not None else None
            self._viewers = [(float(p[0]), float(p[1])) for p in viewers or ()]
            if gt - self._state_gt < EVERY_S and not self._farm:
                return self._state
            self._state = self._build(gt)
            self._state_gt = gt
            return self._state
        except Exception:
            log.exception("JunglerMap.step failed")
            return self._state

    def state(self) -> JunglerMapState:
        return self._state

    # ------------------------------------------------------------------ outputs
    def _prior_grid(self, gt: float) -> np.ndarray:
        s = _static()
        pz = prior_zones(gt, self.his_team, self.prior)
        g = np.zeros((GRID, GRID), np.float32)
        for k, m in self._fine.items():
            w = s["walk"] * m
            tot = float(w.sum())
            if tot > 0:
                g += w * (pz.get(k, 0.0) / tot)
        return g / max(float(g.sum()), 1e-9)

    def _build(self, gt: float) -> JunglerMapState:
        s = _static()
        dead = self._dead_until is not None and gt < self._dead_until
        visible = self._seen_gt is not None and gt - self._seen_gt <= 1.5
        since = None if self._fact is None else max(0.0, gt - self._fact[0])
        prior = self._prior_grid(gt)
        if dead:
            grid = np.zeros_like(prior)
            for k in ("base",):
                grid += self._masks[k] * s["walk"]
            grid /= max(float(grid.sum()), 1e-9)
        elif visible and self._fact is not None:
            grid = (1.0 - SEEN_TRUST) * prior
            u, v = self._fact[1]
            grid[min(GRID - 1, int(v * GRID)), min(GRID - 1, int(u * GRID))] += SEEN_TRUST
        else:
            grid = prior
            stale = since is None or (self.max_fact_s is not None and since > self.max_fact_s)
            pf = None if stale else self._filter(gt)
            if pf is not None:
                pf.step(gt)
                if self._viewers:
                    pf.set_victims(self._viewers)
                    pf.observe_vision(self._viewers)
                if self._farm:
                    pf.observe_farm()
                P = pf.positions()
                xs = np.clip((P[:, 0] * GRID).astype(np.int64), 0, GRID - 1)
                ys = np.clip((P[:, 1] * GRID).astype(np.int64), 0, GRID - 1)
                walk_g = np.bincount(ys * GRID + xs, weights=pf.w, minlength=GRID * GRID)
                walk_g = walk_g.reshape(GRID, GRID).astype(np.float32)
                a = 1.0 - (1.0 - self.prior_floor) * math.exp(-since / max(1.0, self.tau_s))
                grid = (1.0 - a) * walk_g / max(float(walk_g.sum()), 1e-9) + a * prior
        self._farm = False
        grid = grid.astype(np.float32)
        grid /= max(float(grid.sum()), 1e-9)
        zones = {k: float((grid * m).sum()) for k, m in self._masks.items()}
        zs = sum(zones.values()) or 1.0
        zones = {k: v / zs for k, v in zones.items()}
        top = float((grid * s["top"]).sum())
        if visible and not dead and self._fact is not None:     # exact point, not its grid cell
            u, v = self._fact[1]
            z = zone_of((u, v), self.my_team)
            zones = {k: (p if k != z else 0.0) for k, p in zones.items()}
            zs = sum(zones.values()) or 1.0
            zones = {k: (1.0 - SEEN_TRUST) * p / zs for k, p in zones.items()}
            zones[z] = zones.get(z, 0.0) + SEEN_TRUST
            top = SEEN_TRUST * float(u + v < 1.0) + (1.0 - SEEN_TRUST) * 0.5
        p_near = None
        if self._me is not None and not dead:
            p_near = float(grid[self.near_mask(self._me, gt)].sum())
        grid.setflags(write=False)
        shown = self._steady_side(gt, top, visible or dead)
        return JunglerMapState(gt=gt, zones=zones, top_half=top, p_near=p_near, visible=visible, dead=dead,
                               since_fact=since, grid=grid, side_shown=shown)

    def _steady_side(self, gt: float, top: float, off: bool) -> str | None:
        half, val = _side_value(top)
        p = max(top, 1.0 - top)
        cur = self._shown
        if off:
            self._shown = None
        elif cur is None:
            if p >= SIDE_SHOW_P:
                self._shown = (half, val, gt)
        elif half != cur[0]:
            self._shown = (half, val, gt) if p >= SIDE_SHOW_P else None
        elif p < SIDE_HIDE_P:
            self._shown = None
        elif val != cur[1] and gt - cur[2] >= SIDE_HOLD_S:
            self._shown = (half, max(val, int(SIDE_SHOW_P * 100)), gt)
        cur = self._shown
        return None if cur is None else f"{cur[1]} % {cur[0]}"

    @staticmethod
    def near_mask(me: Sequence[float], gt: float, horizon_s: float = NEAR_S) -> np.ndarray:
        from treeaicoach.jungle_graph import speed_at

        s = _static()
        budget = speed_at(gt) * horizon_s + FLASH
        d = np.hypot(s["uu"] - float(me[0]), s["vv"] - float(me[1])) * PATH_FACTOR
        return d <= budget


# ---------------------------------------------------------------------------------- live adapter
def _norm(alias: Any) -> str:
    return "".join(ch for ch in str(alias or "").lower() if ch.isalnum())


class LiveJunglerMap:
    """Feeds a :class:`JunglerMap` from the engine objects (tracker / fog_tracker / Live Client).

    Reads only what fog_tracker already ingested (its anchors: kills, pits, respawns, purchases)
    plus the jungler track (visible?) and the Live Client death flag. Never raises.
    """

    def __init__(self) -> None:
        self.map = JunglerMap()
        self._anchor_t: float | None = None
        self._farm_t: float | None = None
        self._team: str | None = None

    def reset(self) -> None:
        self.map.reset()
        self._anchor_t = self._farm_t = None
        self._team = None

    def update(self, t: float, gt: float | None, tracker: Any, fog: Any, game: Any,
               intel: Any = None) -> JunglerMapState | None:
        try:
            if gt is None or game is None:
                return None
            team = geometry.normalize_team(getattr(getattr(game, "me", None), "team", None))
            if team != self._team and team is not None:
                self._team = team
                self.map.reset(team)
            jg = game.enemy_jungler() if hasattr(game, "enemy_jungler") else None
            alias = _norm(getattr(jg, "champion_alias", "")) if jg is not None else ""
            if not alias:
                return None
            if bool(getattr(jg, "is_dead", False)):
                if self.map._dead_until is None or self.map._dead_until <= gt:
                    timer = getattr(jg, "respawn_timer", None)
                    try:
                        rt = float(timer) if timer is not None and float(timer) > 0 else 30.0
                    except (TypeError, ValueError):
                        rt = 30.0
                    self.map.dead(gt, gt + rt)
            tr = None
            for e in (tracker.enemies(visible_only=False) if tracker is not None and hasattr(tracker, "enemies") else []):
                if _norm(getattr(e, "alias", "")) == alias:
                    tr = e
                    break
            if tr is not None and bool(getattr(tr, "visible", False)) and not getattr(tr, "stacked_with", None):
                pos = getattr(tr, "position", None)
                pos = pos() if callable(pos) else pos
                if pos is not None:
                    self.map.seen(gt, pos)
            anchors = fog.anchors() if fog is not None and hasattr(fog, "anchors") else {}
            a = anchors.get(alias) if isinstance(anchors, dict) else None
            if a is not None and (self._anchor_t is None or a[0] > self._anchor_t + 1e-6):
                self._anchor_t = a[0]
                self.map.anchor(gt - max(0.0, t - a[0]), a[1], a[2])
            ft = getattr(intel, "last_farm_t", None) if intel is not None else None
            if ft is not None and ft != self._farm_t:
                self._farm_t = ft
                self.map.farm()
            me, viewers = None, []
            try:
                mt = tracker.me() if tracker is not None and hasattr(tracker, "me") else None
                p = getattr(mt, "position", None) if mt is not None else None
                me = p() if callable(p) else p
                if me is not None:
                    viewers.append(me)
                for al in (tracker.allies(visible_only=True) if hasattr(tracker, "allies") else []):
                    q = getattr(al, "position", None)
                    q = q() if callable(q) else q
                    if q is not None:
                        viewers.append(q)
            except Exception:
                pass
            return self.map.step(gt, me, viewers)
        except Exception:
            log.debug("LiveJunglerMap.update failed", exc_info=True)
            return None

    def state(self) -> JunglerMapState:
        return self.map.state()


__all__ = ["GRID", "JunglerMap", "JunglerMapState", "LiveJunglerMap", "ZONE_KEYS", "ZONE_FR", "ZONE_PRIOR",
           "FINE_KEYS", "canonical", "fine_zone_of", "prior_zones", "zone_masks", "zone_of"]
