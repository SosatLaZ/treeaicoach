"""Multimodal travel for WoW Forever: walk, known flight paths, boats / zeppelins, hearthstone.

One static table (``DOCKS`` / ``LINES``) is the single source: the addon's ``Data/Travel.lua`` is generated from it
by ``tools/wow_build_travel.py``. Dock positions are world yards ``(continent, x north, y west)`` as in
``data/taxi.json.gz``; crossing times are the client's transport timetables (build 1.60.1.70862: TransportAnimation
periods, arrive/depart per dock), names are the frFR AreaTable names. The expected wait on the quay is half the
period (boats run all day). Pure functions; nothing here raises on bad input.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any

WALK = 7.0            # yd/s (run speed, no mount)
DETOUR = 1.3          # ground path / straight line
FLY_SPEED = 32.0      # yd/s along the flight spline
FLY_HOP = 5.0         # s per hop
FLY_BOARD = 20.0      # s: talk to the flight master, take-off
HEARTH_CAST = 10.0    # s cast
HEARTH_LOAD = 5.0     # s loading screen / landing
MIN_GAIN = 30.0       # s: a transport must beat walking by this much to be proposed
HEARTH_GAIN = 20.0    # s: (4.2 ROUTE42) the hearthstone is used on a leg only when it saves at least this much

# id: (frFR name, continent, x, y)
DOCKS: dict[int, tuple[str, int, float, float]] = {
    1: ("Cabestan", 1, -1005.6, -3841.6),
    2: ("Baie-du-Butin", 0, -14277.7, 582.9),
    3: ("Campement Grom'gol", 0, -12464.0, 231.6),
    4: ("Orgrimmar", 1, 1360.9, -4631.3),
    5: ("Port de Menethil", 0, -3905.2, -585.8),
    6: ("Ile de Theramore", 1, -4016.4, -4740.6),
    7: ("Rut'theran", 1, 8532.0, 1023.7),
    8: ("Auberdine", 1, 6594.4, 759.8),
    9: ("Port de Menethil", 0, -3709.5, -575.1),
    10: ("Auberdine", 1, 6406.2, 823.1),
    11: ("Campement Grom'gol", 0, -12406.9, 211.8),
    12: ("Clairières de Tirisfal", 0, 2062.6, 235.3),
    13: ("Orgrimmar", 1, 1318.1, -4658.0),
    14: ("Clairières de Tirisfal", 0, 2062.4, 293.0),
    15: ("Bastion de Pennelune", 1, -4197.3, 3286.6),
    16: ("Féralas", 1, -4347.8, 2444.4),
    17: ("Austrivage", 0, -1102.6, -554.7),
    18: ("Port Gentepression", 1, -6932.6, -4950.8),
    19: ("Rives Claires", 0, -8232.3, -5800.8),
    20: ("Montagnes d'Alterac", 0, 544.8, 424.2),
    21: ("Île de Zéphras", 2991, 1848.3, 569.1),
    22: ("Mulgore", 1, -802.0, 366.5),
    23: ("Île de Zéphras", 2991, 1952.5, 1019.1),
    24: ("Auberdine", 1, 6548.3, 942.2),
    25: ("Port de Hurlevent", 0, -8654.5, 1344.4),
}

# (kind, faction or None = neutral, period s, ((from dock, to dock, crossing s), ...))
LINES: tuple[tuple[str, str | None, float, tuple[tuple[int, int, float], ...]], ...] = (
    ("boat", None, 351.0, ((1, 2, 107.0), (2, 1, 124.0))),                  # Cabestan ↔ Baie-du-Butin
    ("zeppelin", "Horde", 303.0, ((3, 4, 105.0), (4, 3, 78.0))),            # Grom'gol ↔ Orgrimmar
    ("boat", "Alliance", 329.0, ((5, 6, 104.0), (6, 5, 106.0))),            # Menethil ↔ Theramore
    ("boat", "Alliance", 316.0, ((7, 8, 104.0), (8, 7, 92.0))),             # Rut'theran ↔ Auberdine
    ("boat", "Alliance", 296.0, ((9, 10, 96.0), (10, 9, 79.0))),            # Menethil ↔ Auberdine
    ("zeppelin", "Horde", 333.0, ((11, 12, 117.0), (12, 11, 96.0))),        # Grom'gol ↔ Tirisfal
    ("zeppelin", "Horde", 356.0, ((13, 14, 131.0), (14, 13, 105.0))),       # Orgrimmar ↔ Tirisfal
    ("boat", "Alliance", 317.0, ((15, 16, 60.0), (16, 15, 137.0))),         # Pennelune ↔ côte de Féralas
    ("boat", "Alliance", 486.0, ((9, 17, 142.0), (17, 10, 85.0))),          # Menethil → Austrivage → Auberdine
    ("boat", None, 271.0, ((18, 19, 76.0), (19, 18, 75.0))),                # Gentepression ↔ Rives Claires
    ("zeppelin", "Alliance", 359.0, ((20, 21, 108.0), (21, 20, 131.0))),    # Alterac ↔ Zéphras
    ("zeppelin", "Horde", 320.0, ((22, 23, 84.0), (23, 22, 147.0))),        # Mulgore ↔ Zéphras
    ("boat", "Alliance", 247.0, ((24, 25, 67.0), (25, 24, 60.0))),          # Auberdine ↔ Hurlevent
)

# (4.2 ROUTE42) elevators and the tram as transport edges: (kind, frFR name of end A, frFR name of end B, continent,
# A x, A y, B x, B y, period s, ride s) — world yards like DOCKS; the expected wait is half the period, both ways.
# Only places we can prove go here (measured in game with /tai pos at both ends, or read from the client's
# TransportAnimation / GameObject tables by our tools). Nothing is invented: until measured, the list stays empty and
# the route walks (the relief factor of NavTime covers the climb). TODO (measure in game, Forever 1.60.1):
#   - Les Pitons-du-Tonnerre: 4 elevators (Mulgore floor <-> mesa), period / ride from TransportAnimation
#   - Poste de Librevent (Mille pointes): elevator (canyon floor <-> mesa)
#   - Le Grand ascenseur (Tarides <-> Mille pointes)
#   - Fossoyeuse: 4 elevators (Ruines de Lordaeron <-> city)
#   - Tram des profondeurs (Hurlevent <-> Forgefer): both stations + ride, loading screen included
LIFTS: tuple[tuple[str, str, str, int, float, float, float, float, float, float], ...] = ()

MODE_FR = {"boat": "Bateau", "zeppelin": "Zeppelin", "elevator": "Ascenseur", "tram": "Tram", "fly": "Vol", "hs": "Pierre de foyer", "walk": "Marche"}


def faction_ok(line_faction: str | None, faction: str | None) -> bool:
    return line_faction is None or not faction or line_faction == faction


def transport_edges(faction: str | None = None) -> list[tuple[int, int, str, float]]:
    """(from dock, to dock, kind, seconds = wait + crossing) usable by ``faction``."""
    out = []
    for kind, fac, period, legs in LINES:
        if faction_ok(fac, faction):
            for a, b, cross in legs:
                out.append((a, b, kind, round(period / 2.0 + cross, 1)))
    return out


def lift_edges() -> list[tuple[int, int, str, float]]:
    """(4.2 ROUTE42) (lift index, end 0|1 → end 1|0, kind, seconds = period / 2 + ride) for every lift, both ways."""
    out = []
    for i, (kind, _na, _nb, _c, _ax, _ay, _bx, _by, period, ride) in enumerate(LIFTS):
        sec = round(period / 2.0 + ride, 1)
        out.append((i, 0, kind, sec))
        out.append((i, 1, kind, sec))
    return out


def lift_places() -> list[tuple[tuple[str, int, int], "Place"]]:
    """(4.2 ROUTE42) both ends of every lift: (("lift", index, end), Place)."""
    out = []
    for i, (_k, na, nb, c, ax, ay, bx, by, _p, _r) in enumerate(LIFTS):
        out.append((("lift", i, 0), Place(c, ax, ay, na)))
        out.append((("lift", i, 1), Place(c, bx, by, nb)))
    return out


@dataclass(frozen=True)
class Place:
    c: int
    x: float
    y: float
    name: str = ""


@dataclass
class Leg:
    mode: str                 # walk | fly | boat | zeppelin | hs
    to: Place
    seconds: float
    yards: float = 0.0        # walked yards (walk legs)


@dataclass
class Trip:
    seconds: float
    legs: list[Leg] = field(default_factory=list)

    @property
    def modes(self) -> list[str]:
        return [lg.mode for lg in self.legs]


def walk_seconds(a: Place, b: Place, speed: float = WALK, detour: float = DETOUR) -> float:
    if a.c != b.c:
        return math.inf
    return math.hypot(a.x - b.x, a.y - b.y) * detour / max(1.0, speed)


def flight_table(taxi: dict, known: list[int], flights: dict | None = None) -> tuple[dict[int, Place], dict]:
    """Known taxi nodes → (places, {(a, b): flight seconds}) for every reachable pair (multi-hop included);
    ``flights`` = learned {(a, b): s} for direct legs."""
    nodes = taxi.get("nodes") or {}
    places = {}
    for t in known:
        r = nodes.get(str(t))
        if r:
            places[int(t)] = Place(int(r[1]), float(r[2]), float(r[3]), str(r[0]).split(",")[0])
    edges: dict[tuple[int, int], float] = {}
    for e in taxi.get("edges") or []:
        try:
            a, b, length = int(e[0]), int(e[1]), float(e[2])
        except (TypeError, ValueError, IndexError):
            continue
        if a in places and b in places:
            t = (flights or {}).get((a, b), length / FLY_SPEED + FLY_HOP)
            edges[(a, b)] = min(edges.get((a, b), math.inf), t)
    ids = list(places)
    for k in ids:                       # multi-hop flights are one continuous flight (Floyd–Warshall)
        for i in ids:
            ik = edges.get((i, k))
            if ik is None or i == k:
                continue
            for j in ids:
                kj = edges.get((k, j))
                if kj is not None and i != j and ik + kj < edges.get((i, j), math.inf):
                    edges[(i, j)] = ik + kj
    return places, edges


def fastest(a: Place, b: Place, faction: str | None = None, taxi: dict | None = None, known: list[int] | None = None,
            speed: float = WALK, detour: float = DETOUR, hearth: Place | None = None, hearth_ready: bool = False,
            flights: dict | None = None) -> Trip:
    """Fastest way a → b: min over walking, known flight paths, boats / zeppelins, the hearthstone (from ``a`` only,
    when ready). Dijkstra on {a, b, flight masters, docks, bind}; walking joins any two points of one continent."""
    try:
        return _fastest(a, b, faction, taxi or {}, known or [], speed, detour, hearth, hearth_ready, flights)
    except Exception:
        return Trip(walk_seconds(a, b, speed, detour), [Leg("walk", b, walk_seconds(a, b, speed, detour))])


def _fastest(a, b, faction, taxi, known, speed, detour, hearth, hearth_ready, flights) -> Trip:
    pts: list[Place] = [a, b]
    keys: list[Any] = ["start", "goal"]
    fplaces, fedges = flight_table(taxi, known, flights)
    for t, p in fplaces.items():
        pts.append(p); keys.append(("taxi", t))
    for d, (name, c, x, y) in DOCKS.items():
        pts.append(Place(c, x, y, name)); keys.append(("dock", d))
    for k, p in lift_places():
        pts.append(p); keys.append(k)
    if hearth is not None and hearth_ready:
        pts.append(Place(hearth.c, hearth.x, hearth.y, hearth.name or "auberge")); keys.append("bind")
    index = {k: i for i, k in enumerate(keys)}
    adj: dict[int, list[tuple[int, float, str]]] = {i: [] for i in range(len(pts))}
    for i in range(len(pts)):
        if keys[i] == "goal":
            continue
        for j in range(len(pts)):
            if i != j and keys[j] != "start" and pts[i].c == pts[j].c:
                adj[i].append((j, walk_seconds(pts[i], pts[j], speed, detour), "walk"))
    for (ta, tb), sec in fedges.items():
        adj[index[("taxi", ta)]].append((index[("taxi", tb)], sec + FLY_BOARD, "fly"))
    for da, db, kind, sec in transport_edges(faction):
        adj[index[("dock", da)]].append((index[("dock", db)], sec, kind))
    for li, end, kind, sec in lift_edges():
        adj[index[("lift", li, end)]].append((index[("lift", li, 1 - end)], sec, kind))
    if "bind" in index:
        adj[0].append((index["bind"], HEARTH_CAST + HEARTH_LOAD, "hs"))
    dist = [math.inf] * len(pts)
    prev: list[tuple[int, str, float] | None] = [None] * len(pts)
    dist[0] = 0.0
    heap = [(0.0, 0)]
    while heap:
        d, i = heapq.heappop(heap)
        if d > dist[i]:
            continue
        if i == 1:
            break
        for j, w, mode in adj[i]:
            nd = d + w
            if nd < dist[j] - 1e-9:
                dist[j], prev[j] = nd, (i, mode, w)
                heapq.heappush(heap, (nd, j))
    if dist[1] == math.inf:
        return Trip(math.inf, [])
    legs: list[Leg] = []
    j = 1
    while prev[j] is not None:
        i, mode, w = prev[j]
        yd = math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y) if mode == "walk" else 0.0
        legs.append(Leg(mode, pts[j], w, yd))
        j = i
    legs.reverse()
    direct = walk_seconds(a, b, speed, detour)
    if direct <= dist[1] + MIN_GAIN:            # transports only when they clearly pay off
        return Trip(direct, [Leg("walk", b, direct, math.hypot(a.x - b.x, a.y - b.y))])
    return Trip(dist[1], _merge_walks(legs))


def _merge_walks(legs: list[Leg]) -> list[Leg]:
    out: list[Leg] = []
    for lg in legs:
        if out and lg.mode == "walk" and out[-1].mode == "walk":
            out[-1] = Leg("walk", lg.to, out[-1].seconds + lg.seconds, out[-1].yards + lg.yards)
        else:
            out.append(lg)
    return out


def leg_label(lg: Leg) -> str:
    """« Zeppelin pour Clairières de Tirisfal · 6 min » (FR)."""
    mins = max(1, int(round(lg.seconds / 60.0)))
    if lg.mode == "walk":
        return f"Marche · {mins} min"
    if lg.mode == "hs":
        return f"Pierre de foyer → {lg.to.name or 'auberge'}"
    if lg.mode == "fly":
        return f"Vol pour {lg.to.name} · {mins} min"
    return f"{MODE_FR.get(lg.mode, lg.mode)} pour {lg.to.name} · {mins} min"


# ------------------------------------------------------------------ hub matrix for the solver (data.build_matrix)
def hubs(faction: str | None, taxi: dict, known: list[int], flights: dict | None = None,
         speed: float = WALK, detour: float = DETOUR) -> tuple[list[Any], list[Place], list[list[float]], list[list[int]],
                                                               dict[tuple[int, int], str]]:
    """Hub graph = known flight masters + docks. Returns (keys, places, H all-pairs seconds, nxt next-hop matrix,
    mode of each direct edge). Walking between hubs of one continent is an edge too."""
    fplaces, fedges = flight_table(taxi, known, flights)
    keys: list[Any] = [("taxi", t) for t in fplaces] + [("dock", d) for d in DOCKS] + [k for k, _ in lift_places()]
    places = list(fplaces.values()) + [Place(c, x, y, n) for (n, c, x, y) in DOCKS.values()] + [p for _, p in lift_places()]
    idx = {k: i for i, k in enumerate(keys)}
    m = len(keys)
    H = [[math.inf] * m for _ in range(m)]
    mode: dict[tuple[int, int], str] = {}
    for i in range(m):
        H[i][i] = 0.0
        for j in range(m):
            if i != j and places[i].c == places[j].c:
                H[i][j] = walk_seconds(places[i], places[j], speed, detour)
                mode[(i, j)] = "walk"
    for (ta, tb), sec in fedges.items():
        i, j = idx[("taxi", ta)], idx[("taxi", tb)]
        if sec + FLY_BOARD < H[i][j]:
            H[i][j] = sec + FLY_BOARD; mode[(i, j)] = "fly"
    for da, db, kind, sec in transport_edges(faction):
        i, j = idx[("dock", da)], idx[("dock", db)]
        if sec < H[i][j]:
            H[i][j] = sec; mode[(i, j)] = kind
    for li, end, kind, sec in lift_edges():
        i, j = idx[("lift", li, end)], idx[("lift", li, 1 - end)]
        if sec < H[i][j]:
            H[i][j] = sec; mode[(i, j)] = kind
    nxt = [[j if H[i][j] < math.inf else -1 for j in range(m)] for i in range(m)]
    for k in range(m):
        Hk = H[k]
        for i in range(m):
            hik = H[i][k]
            if hik == math.inf:
                continue
            Hi, Ni = H[i], nxt[i]
            for j in range(m):
                v = hik + Hk[j]
                if v < Hi[j]:
                    Hi[j] = v; Ni[j] = nxt[i][k]
    return keys, places, H, nxt, mode


def hub_path(i: int, j: int, nxt: list[list[int]], places: list[Place], mode: dict) -> list[Leg]:
    """Legs from hub i to hub j along the next-hop matrix (each with its direct-edge mode)."""
    legs: list[Leg] = []
    guard = 0
    while i != j and i >= 0 and guard < 64:
        k = nxt[i][j]
        if k < 0:
            break
        legs.append(Leg(mode.get((i, k), "walk"), places[k], 0.0))
        i, guard = k, guard + 1
    return legs
