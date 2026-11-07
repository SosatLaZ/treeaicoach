"""Synthetic TreeAIDB.export for the planner tests (fake quests, fake names, real map ids)."""

from __future__ import annotations

import random

ZONE, ALT_ZONE = 1424, 1417          # Contreforts de Hautebrande, Hautes-terres d'Arathi


def make_export(nq: int = 30, seed: int = 0, level: int = 26, cap: int = 20, chains: float = 0.3,
                squad: int = 0, zone: int = ZONE, alt_quests: int = 0, in_log: float = 0.3,
                kinds: tuple[str, ...] = ("kill", "item", "obj"), rl_spread: int = 3, max_obj: int = 2) -> dict:
    R = random.Random(seed)
    hubs = [(R.uniform(20, 80), R.uniform(20, 80)) for _ in range(3)]
    quests = []

    def quest(i: int, z: int) -> dict:
        h = R.choice(hubs)
        sx, sy = h[0] + R.gauss(0, 1.5), h[1] + R.gauss(0, 1.5)
        f = (sx, sy) if R.random() < 0.7 else R.choice(hubs)
        lv = level + R.randint(-4, 2)
        nobj = 1 if R.random() < 0.7 or max_obj < 2 else 2
        objs = []
        for _ in range(nobj):
            k = R.choice(kinds)
            o = {"k": k, "id": 1000 + i, "n": R.randint(5, 10), "have": 0, "map": z,
                 "x": round(R.uniform(10, 90), 1), "y": round(R.uniform(10, 90), 1), "r": 60,
                 "sp": R.randint(6, 15), "ml": lv - 1, "mx": lv + 1, "rank": 0}
            if k == "item":
                o["p"] = round(R.uniform(0.3, 0.8), 2)
                o["npc"] = [5000 + i]
            if k == "obj":
                o["n"] = R.randint(3, 8)
            objs.append(o)
        q = {"id": 100 + i, "t": f"Quête test {i}", "lv": lv, "rl": max(1, lv - R.randint(0, rl_spread)),
             "xp": 55 * lv + lv * lv, "st": "avail", "done": 0, "zone": z, "flags": 8, "elite": 0,
             "s": {"map": z, "x": round(sx, 1), "y": round(sy, 1), "k": "npc", "id": 9000 + i},
             "f": {"map": z, "x": round(f[0], 1), "y": round(f[1], 1), "k": "npc", "id": 9100 + i},
             "o": objs}
        return q

    for i in range(nq):
        quests.append(quest(i, zone))
    for i in range(1, nq):
        if R.random() < chains:
            quests[i]["pre"] = [quests[R.randrange(0, i)]["id"]]
    log = 0
    for q in quests:
        if not q.get("pre") and R.random() < in_log and log < cap - 4:
            q["st"] = "log"
            q.pop("s")
            log += 1
    for i in range(alt_quests):
        quests.append(quest(nq + i, ALT_ZONE))
    exp = {
        "v": 1, "made": 1762500000, "char": "Testeur-Royaume", "src": "test",
        "player": {"class": "HUNTER", "level": level, "xp": 0, "xpMax": 36400, "rested": 0,
                   "faction": "Alliance", "map": zone, "x": 50.0, "y": 50.0, "speed": 7.0, "mount": 0,
                   "logMax": cap, "logCount": log, "taxi": [13, 14, 16],
                   "hearth": {"cd": 0, "map": zone, "x": hubs[0][0], "y": hubs[0][1], "name": "Auberge"},
                   "group": 1 + squad},
        "maps": [{"id": zone, "c": 0, "name": "Zone test", "lo": level - 6, "hi": level + 4}],
        "quests": quests,
        "squad": [{"key": f"Ami{j}-Royaume", "class": ("PRIEST", "WARRIOR", "MAGE")[j % 3], "level": level,
                   "map": zone, "x": 52.0, "y": 48.0, "speed": 7.0,
                   "q": [q["id"] for q in quests if q["st"] == "log"][: 5 + j], "done": []}
                  for j in range(squad)],
    }
    return exp


def brute_force(s) -> float:
    """Exact minimum time (CLEAR mode, no hearth) by DP over visited-node masks. ``s`` is a Solver."""
    pb = s.pb
    qs = sorted(s.usable)
    order = [n for q in qs for n in pb.quests[q].nodes]
    idx = {n: i for i, n in enumerate(order)}
    prev = {}
    for q in qs:
        ns = pb.quests[q].nodes
        for a, b in zip(ns, ns[1:]):
            prev.setdefault(b, []).append(a)
        if s.kind[ns[-1]] == 2:
            prev[ns[-1]] = [x for x in ns[:-1]]
    D, START = s.D, s.START
    full = (1 << len(order)) - 1
    frontier = {0: {START: 0.0}}
    for _ in range(len(order)):
        nxt = {}
        for mask, ends in frontier.items():
            logc = pb.log_count
            for n in order:
                if mask >> idx[n] & 1:
                    if s.kind[n] == 0:
                        logc += 1
                    elif s.kind[n] == 2:
                        logc -= 1
            for n in order:
                b = 1 << idx[n]
                if mask & b:
                    continue
                if any(not (mask >> idx[p] & 1) for p in prev.get(n, [])):
                    continue
                q = s.nq[n]
                if s.kind[n] == 0:
                    if logc + 1 > s.cap:
                        continue
                    if any(not (mask >> idx[pb.quests[p].nodes[-1]] & 1) for p in s.pre[q]):
                        continue
                    if s.pre_any[q] and not any(mask >> idx[pb.quests[p].nodes[-1]] & 1 for p in s.pre_any[q]):
                        continue
                svc = s.svc[n] if s.kind[n] == 1 else 5.0
                m2 = mask | b
                tgt = nxt.setdefault(m2, {})
                for e, c in ends.items():
                    v = c + D[e][n] + svc
                    if v < tgt.get(n, float("inf")):
                        tgt[n] = v
        frontier = nxt
    return min(frontier.get(full, {0: float("inf")}).values())
