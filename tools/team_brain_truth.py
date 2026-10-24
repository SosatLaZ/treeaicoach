"""Team brain (team_brain.py) replayed on REAL games: how many personal calls per game, is the budget
kept (one line per friend per 30 s, gank calls first, CALLS_MAX_PER_MIN), and were they right?

    python tools/team_brain_truth.py <games dir>        (records + truth/*.truth.json)

Per game, 1 s ticks: the 4 allies are "friends of the voice channel" (worst case for spam). Positions:
the LCU truth frames (60 s apart) interpolated, the allies as the app sees them (always on the
minimap); the enemies are visible when the app's RECORDED sightings say so (record ``sightings``),
which feeds the jungler map like tools/jungler_map_truth.py (sightings, kills, pits, deaths). Enemy
purchases (Tab) = a truth frame with him in his base. Allies' purchases = a recorded ally position in
his fountain (record ``allies``), items value = his true total gold then. Gank calls on friends are
simulated 6 s before every allied death the enemy jungler took part in (they go first).

Judge: ``jungler`` right when the true jungler came within 0.15 of the friend in the next 25 s
(interpolated minute frames; the base rate over every lane friend-tick with the jungler unseen is printed); ``opp`` right when the opponent was not within 0.2 of the friend in
the next 15 s; ``sync`` right when both had >= 1000 true unspent gold (total - items value).
Development tool only.
"""

from __future__ import annotations

import bisect
import collections
import glob
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treeaicoach import geometry  # noqa: E402
from treeaicoach import team_brain as TB  # noqa: E402
from treeaicoach.discord_bot import CALLS_MAX_PER_MIN, CallFilter  # noqa: E402
from treeaicoach.jungler_map import JunglerMap  # noqa: E402

NEAR_TRUE = 0.15             # "he came": the true jungler within 0.15 of the friend in the next 25 s
BRW = [10, 10, 12, 12, 14, 16, 20, 25, 28, 32.5, 35, 37.5, 40, 42.5, 45, 47.5, 50, 52.5]


def _n(s) -> str:
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def respawn_s(level: int, gt: float) -> float:
    b = BRW[max(1, min(18, int(level or 1))) - 1]
    return b * (1.0 + min(0.5, (gt / 60.0 - 15.0) * 0.0283)) if gt > 900 else b


class Series:
    def __init__(self, pts):
        self.pts = sorted(pts)
        self.ts = [p[0] for p in self.pts]

    def at(self, t):
        i = bisect.bisect_right(self.ts, t)
        if i == 0 or i >= len(self.ts):
            return self.pts[min(max(i - 1, 0), len(self.pts) - 1)][1:] if self.pts else None
        a, b = self.pts[i - 1], self.pts[i]
        w = (t - a[0]) / max(1e-6, b[0] - a[0])
        return tuple(x + (y - x) * w for x, y in zip(a[1:], b[1:]))


class Track:
    def __init__(self, alias):
        self.alias, self.uv, self.visible, self.last_seen = alias, None, False, -1e9

    def position(self):
        return self.uv


class Tracker:
    def __init__(self):
        self.m, self.al, self.en = None, [], []

    def me(self):
        return self.m

    def allies(self, visible_only=False):
        return self.al

    def enemies(self, visible_only=False):
        return self.en


def replay(d: dict, rec: dict) -> dict:
    parts = {p["id"]: p for p in d["participants"]}
    me, team = d["me"], d["my_team"]
    dur = float(d.get("duration") or 0.0)
    pos = {pid: Series([(gt, r[0], r[1]) for gt, row in d["frames"] if str(pid) in row
                        for r in [row[str(pid)]]]) for pid in parts}
    gold = {pid: Series([(gt, row[str(pid)][2], row[str(pid)][4], row[str(pid)][5]) for gt, row in d["frames"]
                         if str(pid) in row]) for pid in parts}
    allies = [pid for pid in parts if parts[pid]["team"] == team and pid != me]
    enemies = [pid for pid in parts if parts[pid]["team"] != team]
    jg = d.get("enemy_jungler")
    dead = collections.defaultdict(list)
    for k in d["kills"]:
        lvl = int((gold[k[2]].at(k[0]) or (0, 0, 1))[2])
        dead[k[2]].append((k[0], k[0] + respawn_s(lvl, k[0])))
    name_of = {_n(parts[p]["alias"]): p for p in parts} | {_n(parts[p]["name"]): p for p in parts}
    sight = collections.defaultdict(list)
    for key, pts in (rec.get("sightings") or {}).items():
        pid = name_of.get(_n(key))
        if pid in enemies:
            sight[pid] += [float(p[0]) for p in pts]
    for pid in sight:
        sight[pid].sort()
    jsight = sorted((float(p[0]), float(p[1]), float(p[2])) for key, pts in (rec.get("sightings") or {}).items()
                    if name_of.get(_n(key)) == jg for p in pts)
    # enemy purchases: a truth frame in his base
    recalls = []
    for gt, row in d["frames"]:
        for pid in enemies:
            r = row.get(str(pid))
            if r and gt > 90:
                z = geometry.classify_zone(r[0], r[1])
                if geometry.is_base(z) and geometry.zone_owner(z) == parts[pid]["team"]:
                    recalls.append((gt, pid))
    # allies' purchases: a recorded position in his fountain -> items value = his total gold then
    buys = collections.defaultdict(list)
    for key, pts in (rec.get("allies") or {}).items():
        pid = name_of.get(_n(key))
        if pid is None:
            continue
        last = -1e9
        for p in pts:
            if geometry.in_fountain(p[1], p[2], team) and p[0] - last > 60:
                last = p[0]
                g = gold[pid].at(p[0])
                if g:
                    buys[pid].append((p[0], g[0] - 100.0))
    gank_t = sorted((k[0] - 6.0, k[2]) for k in d["kills"] if k[2] in allies and (k[1] == jg or jg in k[3]))
    monsters = d["monsters"]

    def obj_states(t):
        out = []
        for key, types, first, again in (("dragon", ("DRAGON",), 300.0, 300.0), ("baron", ("BARON_NASHOR",), 1200.0, 360.0),
                                         ("grubs", ("HORDE",), 480.0, 1e9)):
            kills = [m[0] for m in monsters if m[3] in types and m[0] <= t]
            spawn = first if not kills else (kills[-1] + again if key != "grubs" else 1e9)
            if spawn > 1e8:
                continue
            out.append(SimpleNamespace(key=key, alive=t >= spawn, remaining=max(0.0, spawn - t), next_spawn=spawn))
        return out

    def is_dead(pid, t):
        return any(a <= t < b for a, b in dead[pid])

    def player(pid, t):
        p = parts[pid]
        g = gold[pid].at(t) or (0, 0, 1)
        k = sum(1 for x in d["kills"] if x[0] <= t and x[1] == pid)
        a = sum(1 for x in d["kills"] if x[0] <= t and pid in x[3])
        bb = [v for tt, v in buys[pid] if tt <= t]
        return SimpleNamespace(champion_alias=p["alias"], champion_name=p["name"], position=p["position"],
                               team=p["team"], is_dead=is_dead(pid, t), respawn_timer=0.0, level=int(g[2]),
                               scores={"creepScore": g[1], "kills": k, "assists": a}, items=[],
                               item_value=bb[-1] if bb else 0.0)

    TB._items_value = lambda p: float(getattr(p, "item_value", 0.0))
    clock = [0.0]
    filt = CallFilter(clock=lambda: clock[0])
    brain = TB.TeamBrain()
    jm = JunglerMap()
    jm.reset(team)
    tracker = Tracker()
    tracks = {pid: Track(parts[pid]["alias"]) for pid in parts}
    friends = [parts[p]["alias"] for p in allies]
    said, refused, base = [], 0, []
    times = []
    by_friend = collections.defaultdict(list)
    gi, si, ri = 0, 0, 0
    jfacts = sorted([(k[0], "kill", (k[4], k[5])) for k in d["kills"] if (k[1] == jg or jg in k[3])
                     and parts[k[2]]["team"] == team] + [(a, "dead", b) for a, b in dead[jg]])
    fi = 0
    for t in range(60, int(dur)):
        clock[0] = float(t)
        while si < len(jsight) and jsight[si][0] <= t:
            jm.seen(jsight[si][0], jsight[si][1:])
            si += 1
        while fi < len(jfacts) and jfacts[fi][0] <= t:
            ft, kind, pay = jfacts[fi]
            jm.dead(ft, pay) if kind == "dead" else jm.anchor(ft, pay, kind)
            fi += 1
        for pid in parts:
            tr = tracks[pid]
            tr.uv = pos[pid].at(t)
            if pid in enemies:
                s = sight[pid]
                i = bisect.bisect_right(s, t)
                tr.last_seen = s[i - 1] if i else -1e9
                tr.visible = bool(i) and t - s[i - 1] <= 1.0
            else:
                tr.visible, tr.last_seen = True, float(t)
        tracker.m = tracks[me]
        tracker.al = [tracks[p] for p in allies if not is_dead(p, t)]
        tracker.en = [tracks[p] for p in enemies]
        viewers = [tracks[p].uv for p in allies + [me] if not is_dead(p, t) and tracks[p].uv]
        st = jm.step(float(t), tracks[me].uv, viewers)
        if t % 10 == 0 and t >= 150 and not st.visible and not st.dead and st.grid is not None \
                and t - tracks[jg].last_seen >= 10 and not is_dead(jg, t):
            tick = []
            for p in allies:
                u = tracks[p].uv
                if u is None or is_dead(p, t) or parts[p]["position"] == "JUNGLE" \
                        or geometry.lane_of(geometry.classify_zone(*u)) is None:
                    continue
                pn = float(st.grid[JunglerMap.near_mask(u, t)].sum())
                hit = any(geometry.dist(pos[jg].at(t + dt), pos[p].at(t + dt)) <= NEAR_TRUE for dt in range(0, 26, 2))
                tick.append((pn, hit, geometry.lane_of(geometry.classify_zone(*u)), t - tracks[jg].last_seen))
            for pn, hit, lane, uns in tick:
                other = max((q for q, _h, ln, _u in tick if ln != lane), default=0.0)
                base.append((pn, hit, other, uns))
        while gi < len(gank_t) and gank_t[gi][0] <= t:
            if filt.admit("gank", "lane:" + str(gank_t[gi][1]), target=parts[gank_t[gi][1]]["alias"]):
                times.append(t)
                by_friend[_n(parts[gank_t[gi][1]]["alias"])].append(t)
                said.append(SimpleNamespace(rule="gank", t=t, targets=(_n(parts[gank_t[gi][1]]["alias"]),), text=""))
            gi += 1
        myp = player(me, t)
        snaps = [s for s in rec.get("snapshots") or [] if s.get("game_time", 0) <= t]
        game = SimpleNamespace(me=myp, allies=[player(p, t) for p in allies], enemies=[player(p, t) for p in enemies],
                               current_gold=float(snaps[-1].get("gold") or 0) if snaps else 0.0, is_summoners_rift=True,
                               health=None)
        rc = tuple((_n(parts[p]["alias"]), float(gt)) for gt, p in recalls if 0 <= t - gt <= 60)
        for ln in brain.update(float(t), float(t), game, tracker, friends, recalls=rc, jmap=st,
                               objectives=obj_states(t)):
            ok = filt.admit("team_brain", ln.key, targets=ln.targets, rule=ln.rule)
            brain.ack(ln, ok)
            if not ok:
                refused += 1
                continue
            times.append(t)
            for w in ln.targets:
                by_friend[w].append(t)
            said.append(SimpleNamespace(rule=ln.rule, t=t, targets=ln.targets, text=ln.text, line=ln))
    # budget checks
    per30 = max((sum(1 for y in v if x <= y < x + 30) for v in by_friend.values() for x in v), default=0)
    bf = collections.defaultdict(list)
    for s in said:
        if s.rule != "gank":
            for w in s.targets:
                bf[w].append(s.t)
    per30_brain = max((sum(1 for y in v if x <= y < x + 30) for v in bf.values() for x in v), default=0)
    per_min = max((sum(1 for y in times if x <= y < x + 60) for x in times), default=0)
    after_gank = sum(1 for s in said if s.rule != "gank" and any(
        g.rule == "gank" and 0 <= s.t - g.t < 30 and set(g.targets) & set(s.targets) for g in said))
    # truth judge
    ok = collections.Counter()
    n = collections.Counter()
    alias_pid = {_n(parts[p]["alias"]): p for p in parts}
    for s in said:
        if s.rule == "gank":
            continue
        n[s.rule] += 1
        if s.rule == "jungler":
            hit = any(geometry.dist(pos[jg].at(s.t + dt), pos[alias_pid[w]].at(s.t + dt)) <= NEAR_TRUE
                      for dt in range(0, 26, 2) for w in s.targets if w in alias_pid)
            ok[s.rule] += hit
        elif s.rule == "opp":
            w = alias_pid.get(s.targets[0])
            opps = [p for p in enemies if parts[p]["position"] in (("BOTTOM", "UTILITY") if parts[w]["position"] in
                                                                   ("BOTTOM", "UTILITY") else (parts[w]["position"],))]
            ok[s.rule] += all(geometry.dist(pos[o].at(s.t + dt), pos[w].at(s.t + dt)) > 0.2
                              for o in opps for dt in range(0, 16, 3))
        elif s.rule == "sync":
            good = True
            for w in s.targets:
                pid = alias_pid.get(w)
                g = gold[pid].at(s.t)
                bb = [v for tt, v in buys[pid] if tt <= s.t]
                good &= bool(g) and g[0] - (bb[-1] if bb else 0.0) >= 1000.0
            ok[s.rule] += good
    return {"game": os.path.basename(d.get("record") or "?")[:22], "min": dur / 60.0, "n": dict(n), "ok": dict(ok),
            "gank": sum(1 for s in said if s.rule == "gank"), "refused": refused, "per_friend_30s": per30, "per_friend_30s_brain": per30_brain, "base": base,
            "per_min": per_min, "after_gank": after_gank,
            "texts": [f"{int(s.t) // 60}:{int(s.t) % 60:02d} {s.text}" for s in said if s.rule != "gank"]}


def main(argv: list[str]) -> int:
    root = argv[0] if argv else "."
    rows = []
    for f in sorted(glob.glob(os.path.join(root, "truth", "*.truth.json"))):
        d = json.load(open(f, encoding="utf-8"))
        rp = os.path.join(root, str(d.get("record") or ""))
        if float(d.get("duration") or 0) < 600 or not os.path.isfile(rp):
            continue
        rows.append(replay(d, json.load(open(rp, encoding="utf-8"))))
    tot_n, tot_ok = collections.Counter(), collections.Counter()
    for r in rows:
        tot_n.update(r["n"])
        tot_ok.update(r["ok"])
        print(f"{r['game']:22s} {r['min']:4.0f} min  lines {sum(r['n'].values()):2d} {r['n']}  ganks {r['gank']}  "
              f"max/friend/30s {r['per_friend_30s_brain']} (+gank {r['per_friend_30s']})  max/min {r['per_min']}  after_gank {r['after_gank']}")
    mins = sum(r["min"] for r in rows)
    print(f"\n{len(rows)} games, {mins:.0f} min: {sum(tot_n.values())} lines ({sum(tot_n.values()) / max(1, mins):.2f}/min)")
    for k in sorted(tot_n):
        print(f"  {k:9s} {tot_n[k]:3d}" + (f"  right {tot_ok[k]}/{tot_n[k]}" if k != "objective" else "  (planned, not judged)"))
    base = [b for r in rows for b in r["base"]]
    for lo, oth in ((0.0, 1.0), (0.55, 1.0), (0.75, 1.0), (0.55, 0.3), (0.65, 0.25), (0.75, 0.2), (0.85, 0.15)):
        sel = [b[1] for b in base if b[0] >= lo and b[2] <= oth]
        print(f"  jungler came (base), friend-ticks with P(near) >= {lo:.2f}, other lanes <= {oth:.2f}: "
              f"{sum(sel)}/{len(sel)}")
    print(f"  worst: brain lines per friend in 30 s {max(r['per_friend_30s_brain'] for r in rows)} (with his gank "
          f"calls {max(r['per_friend_30s'] for r in rows)}), per minute "
          f"{max(r['per_min'] for r in rows)} (cap {CALLS_MAX_PER_MIN}), after a gank {sum(r['after_gank'] for r in rows)}")
    if "-v" in argv:
        for r in rows[:3]:
            print(r["game"], *r["texts"][:12], sep="\n  ")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
