"""Route solver: prize-collecting pickup-and-delivery with 3-node requests, scored by simulation.

Model (planner research, report 3): each quest is a request ``pick → objectives → turn-in``. The quest log
is the vehicle capacity, quest chains add precedences, level gates and turn-in XP depend on the simulated
level, and the hearthstone is used opportunistically (60 min cooldown). Modes:

* ``rate``: maximise XP − λ·T. λ is iterated (Dinkelbach: λ ← XP/T, 3–4 rounds), so the result maximises XP/h.
  A quest is kept only when its marginal XP pays for its marginal time.
* ``clear``: do every given quest in the least time (CLEAR / makespan).

Search: sequential cheapest insertion → Or-opt (segments 1–3) + swap with delta screening →
ALNS (Ropke & Pisinger: random / worst / related / chain removal, greedy re-insertion in random or
distance order, simulated annealing, adaptive operator weights). Deterministic for a given seed and
iteration count. Pure Python on lists (no per-element numpy indexing in the inner loops).
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

from treeaicoach.wow.planner import models as M
from treeaicoach.wow.planner.data import Problem

INF = float("inf")
HEARTH_CAST = 10.0
HEARTH_CD = 3600.0
BIG = 1e7


@dataclass
class Result:
    route: list[int]
    score: float
    xp: float                 # value gained (mean member XP in group mode)
    seconds: float
    lam: float = 0.0
    iters: int = 0
    unserved: list[int] = field(default_factory=list)   # quest indices left out

    @property
    def xph(self) -> float:
        return self.xp / self.seconds * 3600.0 if self.seconds > 0 else 0.0


@dataclass
class Sim:
    """Detailed replay of a route (for plan steps)."""
    ok: bool
    xp: float
    seconds: float
    done_at: list[float]          # seconds when each route step is done
    level_at: list[int]
    hearth_before: list[bool]     # the hearthstone is used to reach this step
    level_end: int = 0


class Solver:
    def __init__(self, pb: Problem, mode: str = "rate", quests: set[int] | None = None, p80: bool = False,
                 seed: int = 0, hearth: bool = True) -> None:
        self.pb = pb
        self.mode = mode
        self.rng = random.Random(seed)
        self.D = pb.D
        self.START, self.BIND = pb.START, pb.BIND
        self.hearth = bool(hearth and pb.hearth is not None and pb.D)
        eff = max(0.6, pb.calib.eff)
        nn = len(pb.nodes)
        self.kind = [0] * nn            # 0 pick, 1 obj, 2 turn
        self.nq = [0] * nn
        self.svc = [0.0] * nn
        self.kills = [0.0] * nn
        self.ml = [0] * nn
        self.elite = [False] * nn
        for i, nd in enumerate(pb.nodes):
            self.kind[i] = {"pick": 0, "obj": 1, "turn": 2}[nd.kind]
            self.nq[i] = nd.q
            self.svc[i] = (nd.svc80 if p80 else nd.svc) * eff
            self.kills[i] = nd.kills
            self.ml[i] = nd.ml
            self.elite[i] = nd.elite
        Q = len(pb.quests)
        ids = {q.id: i for i, q in enumerate(pb.quests)}
        self.in_log = [q.in_log for q in pb.quests]
        self.nobj = [sum(1 for n in q.nodes if self.kind[n] == 1) for q in pb.quests]
        self.req = [q.req for q in pb.quests]
        self.qlv = [q.level for q in pb.quests]
        self.qxp = [q.xp for q in pb.quests]
        k = max(1, len(pb.members))
        self.value = [q.holders / k for q in pb.quests]       # share of the squad that gets the quest XP
        self.kill_share = M.GROUP_RATE[min(k, 5)] / k if k > 1 else 1.0
        self.pre: list[list[int]] = [[] for _ in range(Q)]     # all of
        self.pre_any: list[list[int]] = [[] for _ in range(Q)]
        self.excl: list[list[int]] = [[] for _ in range(Q)]
        usable = set(range(Q)) if quests is None else set(quests)
        for i, q in enumerate(pb.quests):
            for p in q.pre:
                if p in ids:
                    self.pre[i].append(ids[p])
                else:
                    usable.discard(i)                           # prerequisite not reachable here
            if q.pre_any:
                alt = [ids[p] for p in q.pre_any if p in ids]
                if not alt:
                    usable.discard(i)
                self.pre_any[i] = alt
            self.excl[i] = [ids[x] for x in q.excl if x in ids]
        changed = True
        while changed:                                          # closure: drop quests whose chain is gone
            changed = False
            for i in list(usable):
                if any(p not in usable for p in self.pre[i]) or (self.pre_any[i] and not any(
                        p in usable for p in self.pre_any[i])):
                    usable.discard(i)
                    changed = True
        self.usable = usable
        self.succ: list[list[int]] = [[] for _ in range(Q)]
        for i in range(Q):
            for p in self.pre[i] + self.pre_any[i]:
                self.succ[p].append(i)
        self.cap = pb.log_cap
        self.lam = 0.0
        # XP tables by level
        self.need = {lv: M.xp_needed(lv) for lv in range(1, 61)}
        self._kx: dict[tuple[int, int, bool], int] = {}
        self.noise = 0.0
        self.n_eval = 0

    # ------------------------------------------------------------------ evaluation
    def kx(self, lvl: int, ml: int, elite: bool) -> int:
        key = (lvl, ml, elite)
        v = self._kx.get(key)
        if v is None:
            v = self._kx[key] = M.kill_xp(lvl, ml, elite)
        return v

    def evaluate(self, route: list[int]) -> tuple[float, float, float]:
        """(score, xp, seconds); score = -inf when the route breaks a rule."""
        self.n_eval += 1
        D, kind, nq, svc = self.D, self.kind, self.nq, self.svc
        Q = len(self.in_log)
        picked = self.in_log[:]
        left = self.nobj[:]
        turned = [False] * Q
        t = 0.0
        lvl = self.pb.level
        xp_in = self.pb.xp
        need = self.need.get(lvl, 0)
        rested = self.pb.rested
        logc = self.pb.log_count
        cap = self.cap
        gained = 0.0
        p = self.START
        hs = self.pb.hearth_cd if self.hearth else INF
        B = self.BIND
        for n in route:
            leg = D[p][n]
            if hs <= t:
                alt = D[B][n] + HEARTH_CAST
                if alt < leg:
                    leg = alt
                    hs = t + HEARTH_CD
            t += leg
            k = kind[n]
            q = nq[n]
            if k == 0:
                if picked[q] or lvl < self.req[q]:
                    return -INF, 0.0, t
                for pr in self.pre[q]:
                    if not turned[pr]:
                        return -INF, 0.0, t
                pa = self.pre_any[q]
                if pa and not any(turned[x] for x in pa):
                    return -INF, 0.0, t
                for x in self.excl[q]:
                    if picked[x]:
                        return -INF, 0.0, t
                picked[q] = True
                logc += 1
                if logc > cap:
                    return -INF, 0.0, t
                t += 5.0
                p = n
                continue
            if not picked[q]:
                return -INF, 0.0, t
            if k == 1:
                t += svc[n]
                left[q] -= 1
                kc = self.kills[n]
                if kc:
                    g = kc * self.kx(lvl, self.ml[n], self.elite[n]) * self.kill_share
                    if rested > 0:
                        bonus = min(rested, g)
                        rested -= bonus
                        g += bonus
                    gained += g
                    xp_in += g
            else:
                if left[q] > 0 or turned[q]:
                    return -INF, 0.0, t
                t += 5.0
                g = M.quest_xp(self.qxp[q], lvl, self.qlv[q]) * self.value[q]
                turned[q] = True
                logc -= 1
                gained += g
                xp_in += g
            while need and xp_in >= need:
                xp_in -= need
                lvl += 1
                need = self.need.get(lvl, 0)
            p = n
        if self.mode == "clear":
            return -t, gained, t
        return gained - self.lam * t, gained, t

    def objective(self, route: list[int]) -> float:
        """Search objective: evaluate() score, minus a big penalty per quest left out in ``clear`` mode."""
        sc = self.evaluate(route)[0]
        if self.mode == "clear" and sc > -INF:
            sc -= BIG * (len(self.usable) - len(set(self.nq[n] for n in route)))
        return sc

    def simulate(self, route: list[int]) -> Sim:
        """Replay with per-step times / levels / hearth use (same rules as :meth:`evaluate`)."""
        D, kind, nq, svc = self.D, self.kind, self.nq, self.svc
        t, lvl, xp_in, gained = 0.0, self.pb.level, float(self.pb.xp), 0.0
        need = self.need.get(lvl, 0)
        rested = self.pb.rested
        p, hs, B = self.START, (self.pb.hearth_cd if self.hearth else INF), self.BIND
        done, levels, hb = [], [], []
        for n in route:
            leg, used = D[p][n], False
            if hs <= t and D[B][n] + HEARTH_CAST < leg:
                leg, used, hs = D[B][n] + HEARTH_CAST, True, t + HEARTH_CD
            t += leg
            q = nq[n]
            if kind[n] == 1:
                t += svc[n]
                kc = self.kills[n]
                if kc:
                    g = kc * self.kx(lvl, self.ml[n], self.elite[n]) * self.kill_share
                    if rested > 0:
                        b = min(rested, g)
                        rested -= b
                        g += b
                    gained += g
                    xp_in += g
            else:
                t += 5.0
                if kind[n] == 2:
                    g = M.quest_xp(self.qxp[q], lvl, self.qlv[q]) * self.value[q]
                    gained += g
                    xp_in += g
            while need and xp_in >= need:
                xp_in -= need
                lvl += 1
                need = self.need.get(lvl, 0)
            done.append(t)
            levels.append(lvl)
            hb.append(used)
            p = n
        sc = self.evaluate(route)[0]
        return Sim(sc > -INF, gained, t, done, levels, hb, lvl)

    # ------------------------------------------------------------------ insertion
    def _delta(self, route: list[int], i: int, x: int) -> float:
        D = self.D
        a = route[i - 1] if i > 0 else self.START
        if i < len(route):
            b = route[i]
            return D[a][x] + D[x][b] - D[a][b]
        return D[a][x]

    def _best_pos(self, route: list[int], x: int, lo: int) -> int:
        best, bi = INF, len(route)
        for i in range(lo, len(route) + 1):
            d = self._delta(route, i, x)
            if d < best:
                best, bi = d, i
        return bi

    def insert(self, route: list[int], q: int, cur: float | None = None, force: bool = False,
               k_first: int = 4) -> tuple[float, list[int]] | None:
        """Cheapest insertion of quest ``q``'s nodes. Returns (score, route) or None (no feasible / no gain)."""
        nodes = self.pb.quests[q].nodes
        if not nodes:
            return None
        pos = {n: i for i, n in enumerate(route)}
        lo = 0
        for pr in self.pre[q] + self.pre_any[q]:
            tn = self.pb.quests[pr].nodes[-1]
            if tn in pos and not self.pre_any[q]:
                lo = max(lo, pos[tn] + 1)
        first = nodes[0]
        cands = sorted(range(lo, len(route) + 1), key=lambda i: self._delta(route, i, first))[:k_first]
        if k_first >= 99:
            cands = list(range(lo, len(route) + 1))
        if self.req[q] > self.pb.level:                         # level gate: also try late positions
            L = len(route)
            cands += [i for i in (L, L - 2, L - 5, (L * 3) // 4, L // 2) if lo <= i <= L and i not in cands]
        best: tuple[float, list[int]] | None = None
        for i in cands:
            r = route[:i] + [first] + route[i:]
            for x in nodes[1:-1]:
                r.insert(self._best_pos(r, x, i + 1), x)
            if len(nodes) > 1:
                last = max(r.index(x) for x in nodes[:-1])
                x = nodes[-1]
                r.insert(self._best_pos(r, x, last + 1), x)
            sc = self.evaluate(r)[0]
            if sc > -INF and self.noise:
                sc += self.noise * (self.rng.random() - 0.5)
            if sc > -INF and (best is None or sc > best[0]):
                best = (sc, r)
        if best is None or k_first < 99:
            # compact variant (pick → objectives → turn-in in a row) at the best few / all places
            spots = range(lo, len(route) + 1) if best is None else cands[:2]
            for i in spots:
                r = route[:i] + list(nodes) + route[i:]
                sc = self.evaluate(r)[0]
                if sc > -INF and (best is None or sc > best[0]):
                    best = (sc, r)
        if best is None and k_first < 99:
            return self.insert(route, q, cur, force, k_first=99)
        if best is None:
            return None
        if self.noise:
            best = (self.evaluate(best[1])[0], best[1])
        if not force and cur is not None and self.mode == "rate" and best[0] <= cur + 1e-9:
            return None
        return best

    def construct(self, order: list[int] | None = None) -> list[int]:
        route: list[int] = []
        cur = self.evaluate(route)[0]
        for q in order if order is not None else self.default_order():
            res = self.insert(route, q, cur, force=self.mode == "clear")
            if res is not None:
                cur, route = res
        return route

    def topo(self, qs: list[int]) -> list[int]:
        out, seen = [], set()
        qset = set(qs)

        def visit(i: int) -> None:
            if i in seen:
                return
            seen.add(i)
            for p in self.pre[i] + self.pre_any[i]:
                if p in qset:
                    visit(p)
            out.append(i)

        for i in qs:
            visit(i)
        return out

    def default_order(self) -> list[int]:
        D, S = self.D, self.START
        qs = sorted(self.usable, key=lambda q: (not self.in_log[q], D[S][self.pb.quests[q].nodes[0]]))
        return self.topo(qs)

    # ------------------------------------------------------------------ local search
    def local_search(self, route: list[int], deadline: float) -> tuple[float, list[int]]:
        cur = self.evaluate(route)[0]
        D, S = self.D, self.START
        improved = True
        while improved and time.monotonic() < deadline:
            improved = False
            n = len(route)
            for L in (1, 2, 3):
                for a in range(0, n - L + 1):
                    if time.monotonic() >= deadline:
                        break
                    seg = route[a:a + L]
                    pa = route[a - 1] if a > 0 else S
                    nb = route[a + L] if a + L < n else None
                    gain = D[pa][seg[0]] + (D[seg[-1]][nb] - D[pa][nb] if nb is not None else 0.0)
                    inner = sum(D[seg[k]][seg[k + 1]] for k in range(L - 1))
                    rest = route[:a] + route[a + L:]
                    for b in range(0, len(rest) + 1):
                        if b == a:
                            continue
                        pb_ = rest[b - 1] if b > 0 else S
                        if b < len(rest):
                            nx = rest[b]
                            cost = D[pb_][seg[0]] + inner + D[seg[-1]][nx] - D[pb_][nx]
                        else:
                            cost = D[pb_][seg[0]] + inner
                        if cost - gain - inner < -1e-6:
                            r = rest[:b] + seg + rest[b:]
                            sc = self.evaluate(r)[0]
                            if sc > cur + 1e-9:
                                route, cur, improved = r, sc, True
                                break
                    if improved:
                        break
                if improved:
                    break
            if not improved:                                    # swap two nodes
                for i in range(n - 1):
                    if time.monotonic() >= deadline:
                        break
                    for j in range(i + 1, min(n, i + 25)):
                        r = route[:]
                        r[i], r[j] = r[j], r[i]
                        if self._len(r) < self._len(route) - 1e-6:
                            sc = self.evaluate(r)[0]
                            if sc > cur + 1e-9:
                                route, cur, improved = r, sc, True
                                break
                    if improved:
                        break
        return cur, route

    def _len(self, r: list[int]) -> float:
        D = self.D
        p, s = self.START, 0.0
        for n in r:
            s += D[p][n]
            p = n
        return s

    # ------------------------------------------------------------------ ALNS
    def quests_in(self, route: list[int]) -> list[int]:
        seen, out = set(), []
        for n in route:
            q = self.nq[n]
            if q not in seen:
                seen.add(q)
                out.append(q)
        return out

    def _closure(self, qs: set[int], present: set[int]) -> set[int]:
        stack = list(qs)
        while stack:
            q = stack.pop()
            for s in self.succ[q]:
                if s in present and s not in qs:
                    qs.add(s)
                    stack.append(s)
        return qs

    def remove(self, route: list[int], qs: set[int]) -> list[int]:
        drop = {n for q in qs for n in self.pb.quests[q].nodes}
        return [n for n in route if n not in drop]

    def destroy(self, op: int, route: list[int], k: int) -> set[int]:
        present = self.quests_in(route)
        if not present:
            return set()
        k = max(1, min(k, len(present)))
        R = self.rng
        if op == 0:                                             # random
            qs = set(R.sample(present, k))
        elif op == 1:                                           # worst: highest saving per XP lost
            base, bxp, bt = self.evaluate(route)
            scored = []
            for q in present:
                r = self.remove(route, self._closure({q}, set(present)))
                sc, xp, t = self.evaluate(r)
                scored.append(((sc - base) + R.random() * 1e-3, q))
            scored.sort(reverse=True)
            qs = {q for _, q in scored[:k]}
        elif op == 2:                                           # related: spatially close to a random seed
            s = R.choice(present)
            sn = self.pb.quests[s].nodes
            D = self.D
            dist = sorted(present, key=lambda q: min(D[sn[0]][self.pb.quests[q].nodes[0]],
                                                     D[sn[-1]][self.pb.quests[q].nodes[-1]]))
            qs = set(dist[:k])
        else:                                                   # chain: a quest and its whole chain
            s = R.choice(present)
            qs = {s} | set(self.pre[s]) | set(self.pre_any[s])
        return self._closure(qs, set(present))

    def repair(self, op: int, route: list[int], qs: list[int], bank: list[int]) -> list[int]:
        cur = self.evaluate(route)[0]
        cand = list(qs)
        if self.mode == "rate" and bank:
            cand += self.rng.sample(bank, min(len(bank), 3))
        if op in (0, 2):
            self.rng.shuffle(cand)
        else:
            D, S = self.D, self.START
            cand.sort(key=lambda q: D[S][self.pb.quests[q].nodes[0]])
        if op == 2:                                             # noisy insertion (diversification)
            self.noise = 0.03 * max(60.0, self._len(route) / max(1, len(route)) * 4)
        try:
            for q in self.topo(cand):
                res = self.insert(route, q, cur, force=self.mode == "clear")
                if res is not None:
                    cur, route = res
        finally:
            self.noise = 0.0
        return route

    def alns(self, route: list[int], seconds: float, max_iters: int = 10 ** 9) -> tuple[float, list[int], int]:
        t_end = time.monotonic() + seconds
        cur_r = route
        cur = self.objective(route)
        best, best_r = cur, route
        w_d, w_r = [1.0] * 4, [1.0] * 3
        s_d, s_r = [0.0] * 4, [0.0] * 3
        c_d, c_r = [0] * 4, [0] * 3
        temp0 = max(1.0, abs(cur) * 0.01)
        it = 0
        last_best = 0
        R = self.rng
        while it < max_iters and time.monotonic() < t_end:
            it += 1
            frac = min(1.0, 1 - (t_end - time.monotonic()) / max(1e-6, seconds))
            temp = temp0 * (0.002 ** frac)
            present = self.quests_in(cur_r)
            if not present:
                bank = sorted(self.usable)
                cand = self.repair(0, [], bank, [])
            else:
                d = R.choices(range(4), w_d)[0]
                rr = R.choices(range(3), w_r)[0]
                k = R.randint(2, max(2, min(12, int(0.3 * len(present)) + 1)))
                qs = self.destroy(d, cur_r, k)
                partial = self.remove(cur_r, qs)
                bank = [q for q in self.usable if q not in present]
                cand = self.repair(rr, partial, sorted(qs), bank)
                c_d[d] += 1
                c_r[rr] += 1
            sc = self.objective(cand)
            curp = cur
            reward = 0.0
            if sc > best + 1e-9:
                best, best_r, reward = sc, cand, 33.0
                last_best = it
                cur, cur_r = sc, cand
            elif sc > curp + 1e-9:
                cur, cur_r, reward = sc, cand, 9.0
            elif sc > -INF and R.random() < math.exp((sc - curp) / temp):
                cur, cur_r, reward = sc, cand, 13.0
            if present:
                s_d[d] += reward
                s_r[rr] += reward
            if it % 50 == 0:
                for ws, ss, cc in ((w_d, s_d, c_d), (w_r, s_r, c_r)):
                    for i in range(len(ws)):
                        if cc[i]:
                            ws[i] = max(0.1, 0.9 * ws[i] + 0.1 * ss[i] / cc[i])
                        ss[i], cc[i] = 0.0, 0
                _, lr = self.local_search(best_r, min(t_end, time.monotonic() + 0.5))
                ls = self.objective(lr)
                if ls > best:
                    best, best_r, last_best = ls, lr, it
                if it - last_best >= 300:                        # stagnation: restart from the best
                    cur, cur_r, last_best = best, best_r, it
        return best, best_r, it


def grind_rate(pb: Problem) -> float:
    """XP per second of grinding same-level mobs (the « grind baseline »)."""
    c = M.kill_cycle(pb.me.cls, pb.level, pb.level, pb.calib, pb.me.dps, 1, pb.zone)
    return M.kill_xp(pb.level, pb.level) / c / max(0.6, pb.calib.eff)


def solve(pb: Problem, mode: str = "rate", seconds: float = 4.0, quests: set[int] | None = None,
          p80: bool = False, seed: int = 0, rounds: int = 3, max_iters: int | None = None,
          hearth: bool = True) -> Result:
    """Best route for ``mode`` within ``seconds`` (λ iteration for ``rate``)."""
    s = Solver(pb, mode, quests, p80, seed, hearth)
    if not s.usable or not pb.D:
        return Result([], 0.0, 0.0, 0.0, unserved=sorted(s.usable))
    t0 = time.monotonic()
    if mode == "rate":
        s.lam = 0.8 * grind_rate(pb)
    route = s.construct()
    budget = max(0.05, seconds - (time.monotonic() - t0))
    nr = rounds if mode == "rate" else 1
    starts = 3 if len(s.usable) <= 20 else 1             # small problems: multi-start is cheap and robust
    iters = 0
    for r in range(nr):
        per = budget / nr / starts
        best_r, best = route, s.objective(route)
        for st in range(starts):
            if st == 0:
                r0 = best_r
            else:
                order = sorted(s.usable)
                s.rng.shuffle(order)
                r0 = s.construct(s.topo(order))
            _, r0 = s.local_search(r0, time.monotonic() + per * 0.3)
            _, r0, it = s.alns(r0, per * 0.7, max_iters if max_iters is not None else 10 ** 9)
            iters += it
            sc = s.objective(r0)
            if sc > best:
                best, best_r = sc, r0
        route = best_r
        sc, xp, t = s.evaluate(route)
        if mode != "rate" or t <= 0:
            break
        new = xp / t
        if abs(new - s.lam) <= 0.02 * max(1e-9, s.lam):
            s.lam = new
            break
        s.lam = new
    sc, xp, t = s.evaluate(route)
    inr = set(s.quests_in(route))
    return Result(route, sc, xp, t, s.lam, iters, sorted(q for q in s.usable if q not in inr))
