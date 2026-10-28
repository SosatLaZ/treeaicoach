"""GANK / DANGER ALERTS on REAL games: per-kind precision, volume, deaths warned.

    python tools/gank_alerts_truth.py <records dir> [<records dir> ...] [--gate] [--detail]

For every record ``*.json`` (and its ``truth/<stem>.truth.json`` when it exists), every alert of
the kinds ``roam_approach``, ``jungler_approach``, ``collapse``, ``personal_danger`` and
``laner_mia`` is scored against where the named enemies REALLY went in the next 20 s:

* positions of an enemy = the record's identified minimap sightings (~2 Hz, what the app saw) +
  the LCU truth anchors (per-minute true positions, kills he took part in);
* **came**: one of the named enemies (any enemy for an anonymous "un ennemi arrive") is within
  :data:`CAME_R` of my position at some instant of ``[t, t + 20 s]`` (or kills me in it);
  **stayed**: positions of them exist in ``[t + 3, t + 20]`` and none came; **unknown** otherwise.
  ``laner_mia`` ("X a disparu") is scored the same way (did he come at me?).
* **precision** = came / (came + stayed);
* **deaths warned**: my deaths (truth kills, else the Live Client kill feed) with an alert of the
  4 danger kinds (level >= 1) 3-25 s before (the ``app`` column of tools/jungler_map_truth.py,
  for every death, and for the deaths the enemy jungler took part in).

The written reason that danger.py shows with a spoken "Recule !" on the same tick is counted
with it (one alert). ``--gate`` replays the recorded alerts through :class:`treeaicoach.threat_gate.ThreatGate` (the
same code the engine runs) with the context the record holds (my positions, sightings, allies,
my HP from the snapshots) and scores what it lets through. Development tool only (aggregated
numbers, never ids).
"""

from __future__ import annotations

import bisect
import glob
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treeaicoach import ground_truth as GT  # noqa: E402

KINDS = ("roam_approach", "jungler_approach", "collapse", "personal_danger", "laner_mia")
DANGER_KINDS = ("jungler_approach", "roam_approach", "collapse", "personal_danger")
CAME_R = 0.08
WINDOW_S = 20.0
STAY_FROM_S = 3.0
WARN_BEFORE_S = (3.0, 25.0)
MY_TOL_S = 2.0


def _norm(s) -> str:
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


class Series:
    def __init__(self, pts):
        self.pts = sorted((float(p[0]), float(p[1]), float(p[2])) for p in pts
                          if isinstance(p, (list, tuple)) and len(p) >= 3)
        self.t = [p[0] for p in self.pts]

    def at(self, t: float, tol: float):
        i = bisect.bisect_left(self.t, t)
        best = None
        for k in (i - 1, i):
            if 0 <= k < len(self.pts) and abs(self.pts[k][0] - t) <= tol:
                if best is None or abs(self.pts[k][0] - t) < abs(best[0] - t):
                    best = self.pts[k]
        return best

    def between(self, a: float, b: float):
        i = bisect.bisect_left(self.t, a)
        j = bisect.bisect_right(self.t, b)
        return self.pts[i:j]


def load_games(dirs: list[str]) -> list[tuple[str, dict, dict | None]]:
    out = []
    seen = set()
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, "*.json"))):
            stem = os.path.basename(f)[:-5]
            if stem in seen:
                continue
            try:
                rec = json.load(open(f, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict) or not isinstance(rec.get("meta"), dict) or not rec.get("alerts"):
                continue
            if (rec.get("duration") or 0) < 300:
                continue
            truth = None
            tp = os.path.join(d, "truth", stem + ".truth.json")
            if os.path.exists(tp):
                try:
                    truth = json.load(open(tp, encoding="utf-8"))
                except (OSError, ValueError):
                    truth = None
            seen.add(stem)
            out.append((stem, rec, truth))
    return out


class Game:
    """Everything the scoring needs from one record (+ truth)."""

    def __init__(self, rec: dict, truth: dict | None):
        self.rec = rec
        self.tr = GT._Truth(truth) if truth else None
        self.me = Series(rec.get("my_positions") or [])
        roster = rec.get("roster") or []
        my_team = (rec.get("meta") or {}).get("team") or ""
        self.enemies = [r.get("alias") for r in roster if isinstance(r, dict) and r.get("team") != my_team
                        and r.get("alias")]
        self.names = {}
        for r in roster:
            if isinstance(r, dict) and r.get("alias"):
                self.names[_norm(r.get("name"))] = r.get("alias")
                self.names[_norm(r.get("alias"))] = r.get("alias")
        self.jungler = next((r.get("alias") for r in roster if isinstance(r, dict) and r.get("team") != my_team
                             and (r.get("has_smite") or r.get("position") == "JUNGLE")), None)
        sight = rec.get("sightings") if isinstance(rec.get("sightings"), dict) else {}
        self.pos: dict[str, Series] = {}
        for a in self.enemies:
            pts = [list(p[:3]) for p in sight.get(a) or [] if isinstance(p, list) and len(p) >= 3]
            if self.tr is not None:
                pid = self.tr.pid_of_alias(a)
                for t, uv, slack, _ex in self.tr.anchors(pid):
                    if slack <= 0.06:
                        pts.append([t, uv[0], uv[1]])
            self.pos[a] = Series(pts)
        self.deaths = self._deaths()
        self.duration = float(rec.get("duration") or 0.0)

    def _deaths(self) -> list[tuple[float, set]]:
        """My deaths: (time, aliases of the enemies involved; empty = unknown)."""
        if self.tr is not None and self.tr.me is not None:
            out = []
            for t, killer, victim, assists, _uv in self.tr.kills:
                if victim == self.tr.me:
                    out.append((t, {self.tr.alias(p) for p in [killer] + list(assists) if p}))
            return out
        meta = self.rec.get("meta") or {}
        me = _norm(str(meta.get("riot_id") or meta.get("summoner_name") or "").split("#")[0])
        out = []
        roster_by_name = {}
        for r in self.rec.get("roster") or []:
            if isinstance(r, dict):
                for k in ("riot_id", "summoner_name"):
                    if r.get(k):
                        roster_by_name[_norm(str(r[k]).split("#")[0])] = r.get("alias")
        for e in self.rec.get("events") or []:
            if isinstance(e, dict) and e.get("EventName") == "ChampionKill" and me \
                    and _norm(e.get("VictimName")) == me:
                inv = {roster_by_name.get(_norm(x)) for x in [e.get("KillerName")] + list(e.get("Assisters") or [])}
                out.append((float(e.get("EventTime") or 0.0), {x for x in inv if x}))
        return out

    def targets(self, kind: str, alias, text: str) -> list[str]:
        if alias and alias in self.pos:
            return [alias]
        if kind == "jungler_approach" and self.jungler:
            return [self.jungler]
        found = []
        low = _norm(text)
        for n, a in self.names.items():
            if a in self.pos and n and n in low and a not in found:
                found.append(a)
        return found or list(self.enemies)

    def verdict(self, t: float, kind: str, alias, text: str) -> str:
        tg = self.targets(kind, alias, text)
        for d, inv in self.deaths:
            if t <= d <= t + WINDOW_S and (not inv or inv & set(tg)):
                return "came"
        seen_far = False
        for a in tg:
            for s, u, v in self.pos[a].between(t - 1.0, t + WINDOW_S):
                m = self.me.at(s, MY_TOL_S)
                if m is None:
                    continue
                if ((u - m[1]) ** 2 + (v - m[2]) ** 2) ** 0.5 <= CAME_R:
                    return "came"
                if s >= t + STAY_FROM_S:
                    seen_far = True
        return "stayed" if seen_far else "unknown"


def alerts_of(rec: dict) -> list[tuple[float, str, int, str, str | None]]:
    out = []
    for a in rec.get("alerts") or []:
        if isinstance(a, list) and len(a) >= 4 and str(a[1]) in KINDS:
            out.append((float(a[0]), str(a[1]), int(a[2] or 0), str(a[3] or ""),
                        a[4] if len(a) >= 5 else None))
    out.sort(key=lambda x: x[0])
    return out


def is_companion(a: tuple, alerts: list) -> bool:
    """danger.py's written reason of a spoken "Recule !" (same tick): part of that alert."""
    return (a[1] == "personal_danger" and a[2] == 1 and (a[3].endswith(" sur toi") or " contre " in a[3])
            and any(b[1] == "personal_danger" and b[2] == 2 and abs(b[0] - a[0]) < 0.05 for b in alerts))


def score(games: list[tuple[str, Game, list]], detail: bool = False) -> dict:
    per = defaultdict(Counter)
    deaths = Counter()
    for stem, g, alerts in games:
        for t, kind, lvl, text, alias in alerts:
            if is_companion((t, kind, lvl, text, alias), alerts):
                continue        # counted with its "Recule !"
            v = g.verdict(t, kind, alias, text)
            per[kind][v] += 1
            per["ALL"][v] += 1
            if detail:
                print(f"  {stem[:16]} {int(t)//60:02d}:{int(t)%60:02d} {kind:16s} L{lvl} {v:7s} {text[:60]}")
        warn = [t for t, k, lvl, _x, _a in alerts if k in DANGER_KINDS and lvl >= 1]
        for d, inv in g.deaths:
            ok = any(d - WARN_BEFORE_S[1] <= w <= d - WARN_BEFORE_S[0] for w in warn)
            deaths["deaths"] += 1
            deaths["warned"] += ok
            if g.jungler and g.jungler in inv:
                deaths["jdeaths"] += 1
                deaths["jwarned"] += ok
    return {"per": per, "deaths": deaths}


def report(title: str, res: dict, n_games: int, minutes: float) -> None:
    per, de = res["per"], res["deaths"]
    print(f"== {title}: {n_games} games, {minutes:.0f} min")
    print(f"{'kind':18s} {'n':>5s} {'/game':>6s} {'came':>5s} {'stay':>5s} {'unk':>5s} {'prec':>5s}")
    for k in KINDS + ("ALL",):
        c = per.get(k) or Counter()
        n = sum(c.values())
        sc = c["came"] + c["stayed"]
        prec = f"{c['came'] / sc:.2f}" if sc else "-"
        print(f"{k:18s} {n:5d} {n / max(1, n_games):6.1f} {c['came']:5d} {c['stayed']:5d} {c['unknown']:5d} {prec:>5s}")
    print(f"deaths warned 3-25 s: {de['warned']}/{de['deaths']}   with the jungler: {de['jwarned']}/{de['jdeaths']}")


def gate_alerts(g: Game, alerts: list) -> list:
    """The recorded alerts the engine's ThreatGate lets through (said now, or held then
    confirmed: emitted at the release time). Context = what the app saw: the named enemies'
    minimap sightings (distance to me)."""
    import heapq

    from treeaicoach import threat_gate as TG
    from treeaicoach.threat_gate import ThreatGate

    gate = ThreatGate()
    sight = g.rec.get("sightings") if isinstance(g.rec.get("sightings"), dict) else {}
    seen = {a: Series(sight.get(a) or []) for a in g.enemies}

    def dist_of(a: str, t: float, tol: float = 1.5):
        p = seen[a].at(t, tol) if a in seen else None
        m = g.me.at(t, MY_TOL_S)
        return None if p is None or m is None else ((p[1] - m[1]) ** 2 + (p[2] - m[2]) ** 2) ** 0.5

    def members_of(kind, text, alias):
        if kind == "jungler_approach" and not alias and g.jungler:
            return (g.jungler,)
        return tuple(g.targets(kind, alias, text)) if (alias or kind == "collapse") else ()

    out = []
    events = []        # (time, order, payload)
    for i, a in enumerate(alerts):
        heapq.heappush(events, (a[0], 1, i))
    batch_t = None
    while events:
        t, typ, i = heapq.heappop(events)
        if typ == 0:   # a hold expires: ask the gate
            for item in gate.due(t, lambda m: dist_of(m, t, 0.8)):
                out.append((t,) + tuple(item[1:]))
            continue
        # the alerts of one tick, highest level first
        same = [i] + [heapq.heappop(events)[2] for _ in range(len(events))
                      if events and events[0][1] == 1 and events[0][0] - t < 0.05]
        for j in sorted(same, key=lambda j: -alerts[j][2]):
            a = alerts[j]
            mem = members_of(a[1], a[3], a[4])
            ds = [d for d in (dist_of(m, a[0]) for m in mem) if d is not None]
            if gate.offer(a[0], a, a[1], a[2], mem, min(ds) if ds else None, companion=is_companion(a, alerts)):
                out.append(a)
            elif gate._held and gate._held[-1][1] is a:
                h = gate._held[-1]
                k = h[0]
                while k <= h[6] + TG.HOLD_MAX_S + 1e-6:     # the engine asks every tick: 1 Hz here
                    heapq.heappush(events, (k, 0, -1))
                    k += 1.0
    out.sort(key=lambda x: x[0])
    return out


def main(argv: list[str]) -> int:
    dirs = [a for a in argv if not a.startswith("--")]
    detail = "--detail" in argv
    raw = load_games(dirs)
    games = [(stem, Game(rec, truth), alerts_of(rec)) for stem, rec, truth in raw]
    groups = {"truth games": [x for x, (_s, _r, tr) in zip(games, raw) if tr is not None],
              "no-truth games": [x for x, (_s, _r, tr) in zip(games, raw) if tr is None]}
    for title, gs in groups.items():
        if not gs:
            continue
        minutes = sum(g.duration for _s, g, _a in gs) / 60.0
        report(title + " (as recorded)", score(gs, detail and "--gate" not in argv), len(gs), minutes)
        if "--gate" in argv:
            gated = [(s, g, gate_alerts(g, a)) for s, g, a in gs]
            report(title + " (through ThreatGate)", score(gated, detail), len(gs), minutes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
