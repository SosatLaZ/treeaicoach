"""Error analysis of our own game records against the League Client ground truth.

A TreeAI game record (``games/<stem>.json``: ``my_positions`` ~1 Hz, enemy ``sightings``
~2 Hz incl. anonymous ``enemy?N`` tracks, ``allies`` ~0.5 Hz, ``selfcheck``) is aligned with
the LCU truth of the same game (``games/truth/<stem>.truth.json``: per-minute positions of
the 10 champions, kills with positions). The truth is sparse (one frame per minute), so
every check is a *reachability* check: a sample of champion X at time ``t`` near a truth
anchor of X at time ``T`` (``|t - T| <= WINDOW_S``) is consistent when it is within
``SLACK + MAX_SPEED * |t - T|`` of it; the excess distance beyond that bound is the error.

Anchors: the minute frames (every champion), and the kills (the victim stands at the kill
position at the kill time). Deaths: a champion is dead from his kill to his estimated respawn
(BRW death timer by level); a sample during that time is a "dead" sighting.

Failure modes (per sample, ranked by count in the report):

* ``wrong_id``   - X drawn where the truth says X cannot be, and an ALIVE champion of the
                   same team can be there (one champion's name on another one's icon);
* ``team_swap``  - ... and only a champion of the OTHER team can be there;
* ``phantom``    - ... and nobody can be there (glyph, ping, stale track);
* ``stale``      - X reported at the same frozen spot for >= STALE_S while the truth moved on;
* ``dead``       - X reported while the truth says he is dead;
* ``gap``        - at a minute frame an alive enemy stands within VISION_R of one of my
                   alive team-mates (so he is on the minimap) and we have no sighting of him
                   within GAP_S; allies and me: no sample within GAP_S at all;
* ``me_far``     - my position off by more than ME_BAD at a minute frame (split: stacked on
                   an ally, i.e. an ally within STACK_D of me in the truth, or not);
* ``anon``       - anonymous enemy samples (``enemy?N``) that a single enemy explains
                   (identity lost) vs that nobody explains (phantom).

    python tools/record_vs_truth.py RECORDS_DIR [--truth DIR] [--json OUT] [-v]
    python tools/record_vs_truth.py rec.json --truth t.truth.json

Records contain other players' riot ids: this tool never prints or writes them (aliases only).
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MAP_UNITS = 14870.0
MAX_SPEED = 425.0 / MAP_UNITS     # ~0.0286 / s (fast champion, out of combat)
FLASH = 0.027
SLACK = 0.03                      # detection noise + icon radius + truth rounding
WINDOW_S = 5.0                    # samples this close to an anchor are checked
BAD_EXCESS = 0.06                 # beyond the reachable bound by this much = error
VISION_R = 0.09                   # an enemy this close to an alive ally is on the minimap
GAP_S = 3.0                       # a sighting this close to a minute frame counts
STALE_S = 6.0                     # frozen this long ...
STALE_EPS = 0.002                 # ... (same position within this)
ME_BAD = 0.05
STACK_D = 0.09                    # one icon diameter: stacked on an ally
#: Death timer (s) by level (BRW, before the late-game time scaling).
BRW = (10, 10, 12, 12, 14, 16, 20, 25, 28, 32.5, 35, 37.5, 40, 42.5, 45, 47.5, 50, 52.5)
DEAD_IN_S = 1.5                   # samples within this after the kill are tolerated (latency)
DEAD_OUT_FRAC = 0.7               # ... and only the first 70 % of the timer is judged (scaling)
FOUNTAIN = {"ORDER": (0.045, 0.955), "CHAOS": (0.955, 0.045)}
FOUNTAIN_R = 0.12                 # samples / anchors this close to the own fountain: recall,
#                                   respawn or teleport may explain any jump (not judged)
MODES = ("wrong_id", "team_swap", "phantom", "stale", "dead", "gap_enemy", "gap_ally", "gap_me",
         "me_far", "me_far_stacked", "anon_lost", "anon_phantom")


# ----------------------------------------------------------------------------- truth model
class Truth:
    def __init__(self, truth: dict) -> None:
        self.parts = {int(p["id"]): p for p in truth.get("participants", [])}
        self.me = int(truth.get("me") or 0)
        self.my_team = truth.get("my_team") or self.parts.get(self.me, {}).get("team")
        self.alias_pid = {p["alias"]: pid for pid, p in self.parts.items()}
        self.frames: list[tuple[float, dict[int, tuple]]] = []
        for t, fr in truth.get("frames", []):
            self.frames.append((float(t), {int(k): tuple(v) for k, v in fr.items()}))
        self.frame_t = [t for t, _ in self.frames]
        self.kills = [(float(k[0]), int(k[1] or 0), int(k[2] or 0), [int(a) for a in (k[3] or [])],
                       float(k[4]), float(k[5])) for k in truth.get("kills", []) if len(k) >= 6]
        # dead intervals per pid
        self.dead: dict[int, list[tuple[float, float]]] = {}
        for t, _killer, victim, _ass, _u, _v in self.kills:
            lvl = self.level(victim, t)
            timer = BRW[max(0, min(len(BRW) - 1, lvl - 1))]
            self.dead.setdefault(victim, []).append((t + DEAD_IN_S, t + DEAD_OUT_FRAC * timer))
        # anchors per pid: (t, u, v)
        self.anchors: dict[int, list[tuple[float, float, float]]] = {pid: [] for pid in self.parts}
        for t, fr in self.frames:
            for pid, val in fr.items():
                if pid in self.anchors and not self.is_dead(pid, t):
                    self.anchors[pid].append((t, float(val[0]), float(val[1])))
        for t, _killer, victim, _ass, u, v in self.kills:
            if victim in self.anchors:
                self.anchors[victim].append((t, u, v))
        for a in self.anchors.values():
            a.sort()
        self.anchor_t = {pid: [x[0] for x in a] for pid, a in self.anchors.items()}

    def team(self, pid: int) -> str | None:
        return self.parts.get(pid, {}).get("team")

    def level(self, pid: int, t: float) -> int:
        i = bisect.bisect_right(self.frame_t, t) - 1
        if i < 0:
            return 1
        val = self.frames[i][1].get(pid)
        try:
            return int(val[5])
        except (TypeError, IndexError, ValueError):
            return 1

    def is_dead(self, pid: int, t: float) -> bool:
        return any(a <= t <= b for a, b in self.dead.get(pid, ()))

    def near_anchor(self, pid: int, t: float) -> tuple[float, float, float] | None:
        a = self.anchors.get(pid) or []
        ts = self.anchor_t.get(pid) or []
        i = bisect.bisect_left(ts, t)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(a) and abs(a[j][0] - t) <= WINDOW_S:
                if best is None or abs(a[j][0] - t) < abs(best[0] - t):
                    best = a[j]
        return best

    def at_home(self, pid: int, u: float, v: float) -> bool:
        f = FOUNTAIN.get(self.team(pid) or "")
        return f is not None and math.hypot(u - f[0], v - f[1]) < FOUNTAIN_R

    def excess(self, pid: int, t: float, u: float, v: float) -> float | None:
        """Distance beyond the reachable bound from the nearest anchor (None: no anchor, or
        a recall / respawn may explain the jump: sample or anchor in the own fountain)."""
        a = self.near_anchor(pid, t)
        if a is None or self.at_home(pid, u, v) or self.at_home(pid, a[1], a[2]):
            return None
        bound = SLACK + MAX_SPEED * abs(t - a[0]) + (FLASH if abs(t - a[0]) > 0.5 else 0.0)
        return max(0.0, math.hypot(u - a[1], v - a[2]) - bound)


# ----------------------------------------------------------------------------- analysis
def _series(raw: Any) -> list[tuple[float, float, float]]:
    out = []
    for s in raw or []:
        try:
            out.append((float(s[0]), float(s[1]), float(s[2])))
        except (TypeError, ValueError, IndexError):
            continue
    out.sort()
    return out


def _stale_runs(series: list[tuple[float, float, float]]) -> list[tuple[float, float, float, float]]:
    """(t0, t1, u, v) runs of a frozen position lasting >= STALE_S."""
    runs = []
    i = 0
    while i < len(series):
        j = i
        while j + 1 < len(series) and math.hypot(series[j + 1][1] - series[i][1],
                                                 series[j + 1][2] - series[i][2]) <= STALE_EPS:
            j += 1
        if series[j][0] - series[i][0] >= STALE_S:
            runs.append((series[i][0], series[j][0], series[i][1], series[i][2]))
        i = j + 1
    return runs


def _who_fits(T: Truth, t: float, u: float, v: float, exclude: int | None = None) -> list[int]:
    return [pid for pid in T.parts if pid != exclude and not T.is_dead(pid, t)
            and (T.excess(pid, t, u, v) or 0.0) <= BAD_EXCESS * 0.5 and T.near_anchor(pid, t) is not None]


def analyse(record: dict, truth: dict, verbose: bool = False) -> dict:
    T = Truth(truth)
    snaps = sorted(((float(x.get("game_time") or 0.0), bool(x.get("is_dead"))) for x in record.get("snapshots") or []
                    if isinstance(x, dict)))
    if snaps and T.me:
        # my deaths: the record's own Live Client snapshots (exact), dead from the first dead
        # snapshot (or the kill just before it) to the last dead one
        iv, start = [], None
        kills_me = [k[0] for k in T.kills if k[2] == T.me]
        for t, d in snaps:
            if d and start is None:
                prev = [k for k in kills_me if t - 15.0 <= k <= t]
                start = (prev[-1] if prev else t) + DEAD_IN_S
            elif not d and start is not None:
                iv.append((start, t - 1.0))
                start = None
        if iv:
            T.dead[T.me] = iv
    me_alias = T.parts.get(T.me, {}).get("alias")
    my_team = T.my_team
    counts = {m: 0 for m in MODES}
    checked = {"enemy": 0, "ally": 0, "me": 0, "anon": 0}
    examples: dict[str, list] = {m: [] for m in MODES}

    def ex(mode: str, item: tuple) -> None:
        counts[mode] += 1
        if len(examples[mode]) < 12:
            examples[mode].append(item)

    named: dict[str, list] = {}
    for key, raw in (record.get("sightings") or {}).items():
        named[key] = _series(raw)
    allies = {k: _series(v) for k, v in (record.get("allies") or {}).items()}
    mine = _series(record.get("my_positions"))
    groups = [("enemy", k, s) for k, s in named.items()] + [("ally", k, s) for k, s in allies.items()]
    if me_alias:
        groups.append(("me", me_alias, mine))
    for side, key, series in groups:
        pid = T.alias_pid.get(key)
        if pid is None:                       # anonymous enemy track
            for t, u, v in series:
                fits = [p for p in _who_fits(T, t, u, v) if T.team(p) != my_team]
                if not any(T.near_anchor(p, t) for p in T.parts):
                    continue
                checked["anon"] += 1
                if fits:
                    ex("anon_lost", (round(t, 1), key, (round(u, 3), round(v, 3)),
                                     [T.parts[p]["alias"] for p in fits]))
                else:
                    ex("anon_phantom", (round(t, 1), key, (round(u, 3), round(v, 3))))
            continue
        for t, u, v in series:
            if T.is_dead(pid, t):
                if side != "me" or True:
                    ex("dead", (round(t, 1), key, (round(u, 3), round(v, 3))))
                continue
            e = T.excess(pid, t, u, v)
            if e is None:
                continue
            checked[side] += 1
            if e <= BAD_EXCESS:
                continue
            others = _who_fits(T, t, u, v, exclude=pid)
            same = [p for p in others if T.team(p) == T.team(pid)]
            a = T.near_anchor(pid, t)
            item = (round(t, 1), key, (round(u, 3), round(v, 3)), (round(a[1], 3), round(a[2], 3)),
                    round(e, 3), [T.parts[p]["alias"] for p in others])
            if side == "me":
                continue                       # (me: judged below at the minute frames)
            if same:
                ex("wrong_id", item)
            elif others:
                ex("team_swap", item)
            else:
                ex("phantom", item)
        for t0, t1, u, v in _stale_runs(series):
            bad = False
            for ft in T.frame_t:
                if t0 + 2.0 <= ft <= t1 and not T.is_dead(pid, ft):
                    e = T.excess(pid, ft, u, v)
                    if e is not None and e > BAD_EXCESS:
                        bad = True
            if bad:
                ex("stale", (round(t0, 1), round(t1, 1), key, (round(u, 3), round(v, 3))))
    # minute frames: gaps and my position
    my_err = []
    for ft, fr in T.frames:
        if ft < 90.0:
            continue                           # loading / fountain: not judged
        mates = [pid for pid in T.parts if T.team(pid) == my_team and not T.is_dead(pid, ft) and pid in fr]
        for pid, val in fr.items():
            if T.is_dead(pid, ft):
                continue
            alias = T.parts[pid]["alias"]
            if T.team(pid) == my_team:
                s = mine if pid == T.me else allies.get(alias, [])
                if not any(abs(x[0] - ft) <= GAP_S for x in s):
                    ex("gap_me" if pid == T.me else "gap_ally", (round(ft), alias))
                continue
            near = any(math.hypot(val[0] - fr[m][0], val[1] - fr[m][1]) <= VISION_R for m in mates)
            if not near:
                continue
            s = named.get(alias, [])
            if not any(abs(x[0] - ft) <= GAP_S for x in s):
                ex("gap_enemy", (round(ft), alias, (round(val[0], 3), round(val[1], 3))))
        # me
        if T.me in fr and not T.is_dead(T.me, ft) and mine:
            i = min(range(len(mine)), key=lambda k: abs(mine[k][0] - ft))
            if abs(mine[i][0] - ft) <= 2.0:
                tu, tv = fr[T.me][0], fr[T.me][1]
                d = math.hypot(mine[i][1] - tu, mine[i][2] - tv)
                stacked = any(math.hypot(tu - fr[m][0], tv - fr[m][1]) <= STACK_D
                              for m in mates if m != T.me)
                my_err.append((d, stacked))
                checked["me"] += 1
                if d > ME_BAD:
                    ex("me_far_stacked" if stacked else "me_far",
                       (round(ft), (round(mine[i][1], 3), round(mine[i][2], 3)), (round(tu, 3), round(tv, 3)),
                        round(d, 3)))
    errs = sorted(d for d, _s in my_err)
    st_errs = sorted(d for d, s in my_err if s)
    sc = record.get("selfcheck") or {}
    health = [(p.get("rule"), p.get("first_gt"), p.get("count"), p.get("status"))
              for p in sc.get("problems", [])] if isinstance(sc, dict) else []
    return {
        "app_version": (record.get("meta") or {}).get("app_version"),
        "me": me_alias, "role": T.parts.get(T.me, {}).get("position"),
        "duration": truth.get("duration"), "checked": checked, "counts": counts, "examples": examples,
        "me_err50": errs[len(errs) // 2] if errs else None,
        "me_err_stacked50": st_errs[len(st_errs) // 2] if st_errs else None,
        "me_stacked_n": len(st_errs), "health": health,
    }


# ----------------------------------------------------------------------------- report
def _find_truth(rec: Path, truth_dir: Path) -> Path | None:
    stem = rec.stem
    for p in sorted(truth_dir.glob("*.truth.json")):
        if p.name.endswith(stem + ".truth.json"):
            return p
    return None


def report(results: dict[str, dict], verbose: bool = False) -> str:
    L = []
    cols = ("wrong_id", "team_swap", "phantom", "stale", "dead", "gap_enemy", "gap_ally", "gap_me",
            "me_far", "me_far_stacked", "anon_lost", "anon_phantom")
    head = f"{'game':24s} {'ver':6s} {'chk e/a/me':>12s} " + " ".join(f"{c[:9]:>9s}" for c in cols) + \
        "  me50  meSt50(n)"
    L.append(head)
    tot = {c: 0 for c in cols}
    for name, r in results.items():
        ck = r["checked"]
        for c in cols:
            tot[c] += r["counts"][c]
        f = lambda x: "  -  " if x is None else f"{x:.3f}"  # noqa: E731
        L.append(f"{name[:24]:24s} {str(r['app_version'])[:6]:6s} "
                 f"{ck['enemy']:>4d}/{ck['ally']:>3d}/{ck['me']:>2d} "
                 + " ".join(f"{r['counts'][c]:>9d}" for c in cols)
                 + f"  {f(r['me_err50'])} {f(r['me_err_stacked50'])}({r['me_stacked_n']})")
    L.append(f"{'TOTAL':24s} {'':6s} {'':>12s} " + " ".join(f"{tot[c]:>9d}" for c in cols))
    L.append("")
    L.append("Failure modes ranked: " + ", ".join(f"{c} {n}" for c, n in sorted(tot.items(), key=lambda kv: -kv[1]) if n))
    for name, r in results.items():
        if r["health"]:
            L.append(f"{name}: self-check " + "; ".join(f"{a}@{b}s x{c}: {d}" for a, b, c, d in r["health"]))
    if verbose:
        for name, r in results.items():
            L.append(f"\n== {name}")
            for m, exs in r["examples"].items():
                for e in exs:
                    L.append(f"  {m:14s} {e}")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("records", help="a record .json or a folder of records")
    ap.add_argument("--truth", help="truth file or folder (default: RECORDS/truth or ../truth)")
    ap.add_argument("--json")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    src = Path(a.records)
    recs = sorted(src.glob("*.json")) if src.is_dir() else [src]
    tdir = Path(a.truth) if a.truth else (src / "truth" if (src / "truth").is_dir() else src.parent / "truth")
    results = {}
    for rec in recs:
        tp = tdir if tdir.is_file() else _find_truth(rec, tdir)
        if tp is None:
            continue
        try:
            record = json.loads(rec.read_text(encoding="utf-8"))
            truth = json.loads(tp.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"{rec.name}: unreadable ({exc})")
            continue
        results[rec.stem] = analyse(record, truth, a.v)
    print(report(results, a.v))
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
