"""Write wow_addon/TreeAI/Data/Travel.lua (flight masters, flight legs, boats / zeppelins) for the in-game route.

Single source: ``treeaicoach/wow/planner/transport.py`` (docks, lines, crossing times) and the shipped
``treeaicoach/wow/data/taxi.json.gz`` (Blizzard client tables TaxiNodes / TaxiPath, build 1.60.1.70862).
Every place gets its world position (continent, x north, y west — the UnitPosition frame) and, for guidance, the
smallest zone map holding it with x / y in percent. Usage: ``python tools/wow_build_travel.py`` (no network).
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from treeaicoach.wow.planner import transport as TR  # noqa: E402
from treeaicoach.wow.planner.data import taxi_data  # noqa: E402

OUT = os.path.join(ROOT, "wow_addon", "TreeAI", "Data", "Travel.lua")
CONTINENT_MAPS = {1414, 1415, 1945, 947}


# docks whose smallest containing zone map is a neighbour (zone rectangles overlap)
DOCK_ZONE = {1: 1413, 4: 1411, 6: 1445, 8: 1439, 10: 1439, 12: 1420, 14: 1420, 22: 1412, 24: 1439}


# (4.1 BUGHUNT) names the frFR client tables leave in English: shown in French (never an English name on screen)
NAME_FR = {"Thunder Bluff": "Les Pitons-du-Tonnerre", "Darkshire": "Sombre-Comté", "Feathermoon": "Bastion de Pennelune",
           "Ratchet": "Cabestan"}


def _words(s: str) -> set[str]:
    s = str(s).lower().replace("hillsbrad", "hautebrande").replace(",", " ").replace("'", " ")
    return {w for w in s.split() if len(w) > 3}


def zone_of(maps: dict, c: int, wx: float, wy: float, hint: str = "", force: int = 0) -> tuple[int, float, float]:
    """(uiMapID, x%, y%): the forced map, else a containing zone whose name shares a word with ``hint``, else
    the smallest containing zone."""
    best = None
    hw = _words(hint)
    for mid, r in maps.items():
        if int(mid) in CONTINENT_MAPS or int(r[1]) != c:
            continue
        x0, y0, x1, y1 = (float(v) for v in r[2:6])
        if x1 == x0 or y1 == y0:
            continue
        my, mx = (wx - x0) / (x1 - x0), (wy - y0) / (y1 - y0)
        if 0 <= mx <= 1 and 0 <= my <= 1:
            area = abs((x1 - x0) * (y1 - y0))
            rank = 0 if int(mid) == force else (1 if hw & _words(r[0]) else 2)
            if best is None or (rank, area) < best[0]:
                best = ((rank, area), int(mid), round(mx * 100, 1), round(my * 100, 1))
    return (best[1], best[2], best[3]) if best else (0, 0.0, 0.0)


def q(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def build() -> str:
    tx = taxi_data()
    maps = tx.get("maps") or {}
    out = [
        "-- GÉNÉRÉ par tools/wow_build_travel.py — ne pas éditer à la main.",
        f"-- Sources : tables client Blizzard (WoW Forever, build {tx.get('build', '?')}) TaxiNodes / TaxiPath /",
        "-- TransportAnimation, noms frFR (AreaTable) ; table des bateaux / zeppelins : treeaicoach/wow/planner/transport.py.",
        "-- Position monde { continent, x (nord), y (ouest) } = repère de UnitPosition ; map/mx/my = carte de zone en %.",
        "-- docks[id] = { nom, c, x, y, map, mx, my } · lines = { { type, faction|false, période s, { {de, vers, traversée s}, … } } }",
        "-- taxi[nodeID] = { nom, c, x, y, map, mx, my, faction } · flights = { {de, vers, secondes}, … } (vols directs)",
        "local T = TreeAI",
        'if type(T) ~= "table" then return end',
        "T.Data = T.Data or {}",
        "T.Data.Travel = {",
        f"  build = {q(tx.get('build', ''))}, walk = {TR.WALK}, detour = {TR.DETOUR}, board = {TR.FLY_BOARD},",
        f"  hearthCast = {TR.HEARTH_CAST}, hearthLoad = {TR.HEARTH_LOAD}, minGain = {TR.MIN_GAIN},",
        "  docks = {",
    ]
    for d, (name, c, x, y) in sorted(TR.DOCKS.items()):
        mid, mx, my = zone_of(maps, c, x, y, name, DOCK_ZONE.get(d, 0))
        out.append(f"    [{d}] = {{ {q(NAME_FR.get(name, name))}, {c}, {x}, {y}, {mid}, {mx}, {my} }},")
    out.append("  },")
    out.append("  lines = {")
    for kind, fac, period, legs in TR.LINES:
        ls = ", ".join(f"{{ {a}, {b}, {cross:g} }}" for a, b, cross in legs)
        out.append(f"    {{ {q(kind)}, {q(fac) if fac else 'false'}, {period:g}, {{ {ls} }} }},")
    out.append("  },")
    out.append("  taxi = {")
    nodes = tx.get("nodes") or {}
    for nid in sorted(nodes, key=int):
        r = nodes[nid]
        c, x, y = int(r[1]), float(r[2]), float(r[3])
        mid, mx, my = zone_of(maps, c, x, y, str(r[0]))
        name = str(r[0]).split(",")[0]
        name = NAME_FR.get(name, name)
        out.append(f"    [{int(nid)}] = {{ {q(name)}, {c}, {x}, {y}, {mid}, {mx}, {my}, {q(r[4] or '')} }},")
    out.append("  },")
    out.append("  flights = {")
    row = []
    for e in tx.get("edges") or []:
        a, b, length = int(e[0]), int(e[1]), float(e[2])
        row.append(f"{{ {a}, {b}, {round(length / TR.FLY_SPEED + TR.FLY_HOP, 2):g} }}")
        if len(row) == 6:
            out.append("    " + ", ".join(row) + ",")
            row = []
    if row:
        out.append("    " + ", ".join(row) + ",")
    out.append("  },")
    out.append("}")
    return "\n".join(out) + "\n"


def main() -> int:
    text = build()
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"wrote {OUT} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
