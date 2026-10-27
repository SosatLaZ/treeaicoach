"""OBJECTIVE PLANNER on REAL games: does being at the pit with a fresh item, the back timing, a river
ward or a death in the last 40 s change who takes the epic objective?

    python tools/objective_truth.py <games dir>      (records *.json + truth/*.truth.json)

One row per epic objective spawn of the season 2026 schedule (objectives.OBJECTIVE_SCHEDULE: dragon
5:00 + 5:00 after a kill, Elder 6:00 after the soul, grubs 8:00, Herald 15:00, Baron 20:00 + 6:00),
the taker from the LCU truth monsters (none = despawned / not taken in the game). For me (the
recorded player, my role must play the objective: objective_planner.ROLE_PLAYS):

* ``at_pit``: my position (record 1 Hz + truth frames) within :data:`PIT_R` of the pit at the spawn;
* ``fresh``: my item gold (record snapshots) rose by >= :data:`FRESH_GOLD` in the 150 s before it;
* back timing: my last fountain visit (alive) in [S-150, S-50] (``early``: a back that lands) or in
  [S-50, S] (``late``) or none;
* ``ward``: my ward score rose in [S-75, S-10];
* ``died40``: I (``me``) / an ally (``team``) died in [S-40, S] (truth kills), with the fight even
  at S-40 (same alive count, true total gold gap < 3 000) or not.
Each bin prints n, our take rate and their take rate. Frames are 60 s apart (gold / alive counts at
S-40 are samples); positions of the recorded player are 1 Hz.
"""

from __future__ import annotations

import bisect
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from treeaicoach import geometry  # noqa: E402
from treeaicoach import ground_truth as GT  # noqa: E402
from treeaicoach import macro as M  # noqa: E402
from treeaicoach import objective_planner as OP  # noqa: E402
from treeaicoach.scoreboard import items_gold  # noqa: E402
from team_calls_truth import respawn_s  # noqa: E402

PIT_R = 0.2
FRESH_GOLD = 500
EVEN_GOLD = 3000


def instances(monsters: list, duration: float, mode: str = "CLASSIC") -> list[dict]:
    """``{key, spawn, take, team}`` of every spawn (team "" = not taken). Swiftplay
    (objectives.MODE_SCHEDULES): no grubs / Herald, 2 dragons, Elder 15:00 (+6:00), Baron 12:00 (+5:00)."""
    if str(mode).upper() == "SWIFTPLAY":
        return _swift_instances(monsters, duration)
    out = []
    drag = sorted((m for m in monsters if m[3] in ("DRAGON", "ELDER_DRAGON")), key=lambda m: m[0])
    s, souls = 300.0, {}
    for m in drag:
        if m[0] < s - 5:
            continue
        key = "elder" if m[3] == "ELDER_DRAGON" or "ELDER" in str(m[4]) else "dragon"
        out.append({"key": key, "spawn": s, "take": m[0], "team": m[2] or ""})
        if key == "dragon" and m[2]:
            souls[m[2]] = souls.get(m[2], 0) + 1
        s = m[0] + (360.0 if max(souls.values(), default=0) >= 4 else 300.0)
    if s < duration:
        out.append({"key": "elder" if max(souls.values(), default=0) >= 4 else "dragon", "spawn": s,
                    "take": None, "team": ""})
    horde = sorted((m for m in monsters if m[3] == "HORDE"), key=lambda m: m[0])
    if duration > 480:
        teams = [m[2] for m in horde if m[2]]
        team = max(set(teams), key=teams.count) if teams else ""
        out.append({"key": "grubs", "spawn": 480.0, "take": horde[0][0] if horde else None, "team": team})
    if duration > 900:
        h = [m for m in monsters if m[3] == "RIFTHERALD" and m[2]]
        out.append({"key": "herald", "spawn": 900.0, "take": h[0][0] if h else None, "team": h[0][2] if h else ""})
    s = 1200.0
    for m in sorted((m for m in monsters if m[3] == "BARON_NASHOR"), key=lambda m: m[0]):
        out.append({"key": "baron", "spawn": s, "take": m[0], "team": m[2] or ""})
        s = m[0] + 360.0
    if s < duration:
        out.append({"key": "baron", "spawn": s, "take": None, "team": ""})
    return [o for o in out if o["spawn"] + 30 < duration]


def _swift_instances(monsters: list, duration: float) -> list[dict]:
    out = []
    drag = sorted((m for m in monsters if m[3] == "DRAGON" and "ELDER" not in str(m[4])), key=lambda m: m[0])
    s = 300.0
    for m in drag[:2]:
        out.append({"key": "dragon", "spawn": s, "take": m[0], "team": m[2] or ""})
        s = m[0] + 300.0
    if len(drag) < 2 and s < 900.0:
        out.append({"key": "dragon", "spawn": s, "take": None, "team": ""})
    for key, first, resp, sel in (("elder", 900.0, 360.0, lambda m: m[3] == "DRAGON" and "ELDER" in str(m[4])),
                                  ("baron", 720.0, 300.0, lambda m: m[3] == "BARON_NASHOR")):
        s = first
        for m in sorted((m for m in monsters if sel(m)), key=lambda m: m[0]):
            out.append({"key": key, "spawn": s, "take": m[0], "team": m[2] or ""})
            s = m[0] + resp
        if s < duration:
            out.append({"key": key, "spawn": s, "take": None, "team": ""})
    return [o for o in out if o["spawn"] + 30 < duration]


def _dead_until(tr: GT._Truth, pid: int) -> list[tuple[float, float]]:
    out = []
    for t, _k, victim, _a, _uv in tr.kills:
        if victim == pid:
            fr = tr.frame_at(t / 60.0, pid) or [0, 0, 0, 0, 0, 1]
            out.append((t, t + respawn_s(int(fr[5] or 1), t)))
    return out


def _alive(dead: list, t: float) -> bool:
    return not any(a <= t < b for a, b in dead)


def rows_of(rec: dict, truth: dict) -> list[dict]:
    tr = GT._Truth(truth)
    me = tr.me
    my_team = truth.get("my_team")
    role = (rec.get("summary") or {}).get("position") or ""
    parts = {p["id"]: p for p in truth.get("participants") or []}
    pos = GT._my_pos_fn(rec, tr)
    snaps = rec.get("snapshots") or []
    st = [s.get("game_time", 0.0) for s in snaps]
    ig = [items_gold(s.get("items") or []) for s in snaps]
    ws = [float(s.get("ward_score") or 0.0) for s in snaps]
    my_dead = _dead_until(tr, me)
    # a back = alive in my fountain, not just respawned (20 s after a death timer)
    fountain = [p[0] for p in rec.get("my_positions") or []
                if geometry.in_fountain(p[1], p[2], my_team) and _alive(my_dead, p[0])
                and not any(b <= p[0] <= b + 20.0 for _a, b in my_dead)]
    # a control ward placed = one less 2055 in my inventory between two snapshots, alive
    cw_used = [snaps[i]["game_time"] for i in range(1, len(snaps))
               if (snaps[i - 1].get("items") or []).count(2055) > (snaps[i].get("items") or []).count(2055)
               and not snaps[i].get("is_dead")]

    def at(series: list, t: float) -> float:
        i = bisect.bisect_right(st, t) - 1
        return series[max(0, i)] if series else 0.0

    out = []
    for o in instances(truth.get("monsters") or [], float(truth.get("duration") or 0),
                       str(truth.get("game_mode") or "CLASSIC")):
        key, s = o["key"], o["spawn"]
        if role not in OP.ROLE_PLAYS.get(key, ()):
            continue
        pit = M.PIT_UV[key]
        p = pos(s)
        backs = [t for t in fountain if s - 150 <= t <= s]
        last = max(backs) if backs else None
        back = "none" if last is None else ("early" if last <= s - 50 else "late")
        # the fight at S-40 (truth frame of that minute)
        fr = None
        for gt, row in tr.frames:
            if gt <= s - 40:
                fr = row
        n_us = n_them = 0
        gd = 0.0
        for pid, pp in parts.items():
            if fr is None or str(pid) not in fr:
                continue
            if not _alive(_dead_until(tr, pid), s - 40):
                continue
            if pp["team"] == my_team:
                n_us += 1
                gd += fr[str(pid)][2]
            else:
                n_them += 1
                gd -= fr[str(pid)][2]
        ally_d = sum(1 for t, _k, v, _a, _uv in tr.kills if s - 40 <= t <= s and parts.get(v, {}).get("team") == my_team)
        out.append({
            "key": key, "spawn": s, "taken": 1 if o["team"] == my_team else (-1 if o["team"] else 0),
            "known": p is not None, "at_pit": p is not None and geometry.dist(p, pit) < PIT_R,
            "alive": _alive(my_dead, s),
            "fresh": at(ig, s) - at(ig, s - 150) >= FRESH_GOLD, "back": back,
            "ward": at(ws, s - 10) - at(ws, s - 75) >= 0.9,
            "me_died40": any(s - 40 <= a <= s for a, _b in my_dead), "team_died40": ally_d > 0,
            "even": n_us == n_them and abs(gd) < EVEN_GOLD,
            "edge": "even" if (n_us == n_them and abs(gd) < EVEN_GOLD) else
                    ("ahead" if (n_us > n_them or (n_us == n_them and gd >= EVEN_GOLD)) else "behind"),
            "control": any(s - 90 <= t <= s for t in cw_used), "gold90": at([float(s0.get("gold") or 0) for s0 in snaps], s - 90),
        })
    return out


def _rate(rows: list[dict]) -> str:
    n = len(rows)
    us = sum(1 for r in rows if r["taken"] > 0)
    them = sum(1 for r in rows if r["taken"] < 0)
    return f"n={n:3d} ours={us:2d} ({us / max(1, n):.0%}) theirs={them:2d} ({them / max(1, n):.0%})"


def report(rows: list[dict]) -> dict:
    out: dict = {}

    def line(name: str, sel: list[dict]) -> None:
        print(f"   {name:34s} {_rate(sel)}")
        out[name] = {"n": len(sel), "ours": sum(1 for r in sel if r["taken"] > 0),
                     "theirs": sum(1 for r in sel if r["taken"] < 0)}

    k = [r for r in rows if r["known"] and r["alive"]]
    print(f"{len(rows)} objective spawns my role plays ({len(k)} with my position known and me alive)")
    print("1. at the pit at the spawn x fresh item (150 s)")
    line("at pit + fresh item", [r for r in k if r["at_pit"] and r["fresh"]])
    line("at pit, no fresh item", [r for r in k if r["at_pit"] and not r["fresh"]])
    line("not at pit", [r for r in k if not r["at_pit"]])
    print("2. my back before the spawn")
    for b in ("early", "late", "none"):
        sel = [r for r in rows if r["back"] == b]
        line(f"back {b}", sel)
        pres = [r for r in sel if r["known"]]
        print(f"      at pit at the spawn: {sum(r['at_pit'] for r in pres)}/{len(pres)}")
        out[f"back {b}"]["at_pit"] = [sum(r["at_pit"] for r in pres), len(pres)]
    print("3. my ward score rose in [S-75, S-10]")
    line("ward", [r for r in rows if r["ward"]])
    line("no ward", [r for r in rows if not r["ward"]])
    line("control ward placed [S-90, S]", [r for r in rows if r["control"]])
    line("no control ward", [r for r in rows if not r["control"]])
    print("4. deaths in [S-40, S] (fight at S-40: alive counts + true total gold)")
    line("all: I died", [r for r in rows if r["me_died40"]])
    line("all: nobody of us died", [r for r in rows if not r["team_died40"]])
    for edge in ("even", "ahead", "behind"):
        tag = edge
        sel = [r for r in rows if r["edge"] == edge]
        line(f"{tag}: I died", [r for r in sel if r["me_died40"]])
        line(f"{tag}: an ally died (not me)", [r for r in sel if r["team_died40"] and not r["me_died40"]])
        line(f"{tag}: nobody of us died", [r for r in sel if not r["team_died40"]])
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    rows = []
    for f in sorted(glob.glob(os.path.join(argv[0], "*.json"))):
        tf = os.path.join(argv[0], "truth", os.path.basename(f).replace(".json", ".truth.json"))
        if not os.path.exists(tf):
            continue
        try:
            rec, truth = json.load(open(f, encoding="utf-8")), json.load(open(tf, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows += rows_of(rec, truth)
    report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
