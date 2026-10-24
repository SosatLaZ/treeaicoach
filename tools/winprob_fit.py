"""Check / re-fit the live win-probability model (:mod:`treeaicoach.hype`) on real games.

Usage::

    python tools/winprob_fit.py <games folder> [--truth <folder of *.truth.json>]

For every recorded rift game that has an LCU truth file (end-of-game timeline: per-minute
``totalGold``, xp, CS and level of all 10 players, kills, buildings, monsters) and a known result,
it rebuilds the model inputs at 10 / 15 / 20 min twice:

* ``exact`` - true team gold / level / CS from the League Client timeline;
* ``live``  - what the Live Client exposes in game (Tab item-gold diff + CS / level diffs from the
  record's scoreboard timeline, kills / towers / dragons), combined by
  :func:`treeaicoach.hype.gold_lead_estimate`.

It prints Brier score and accuracy (p >= 50 % on the winner) of the current weights, then of a
few BOUNDED one- or two-parameter variants scored by leave-one-GAME-out (each game is predicted by
weights fitted without it), so 20 games cannot be over-fitted. No identity is read or printed.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treeaicoach import hype as H  # noqa: E402

TIMES = (600.0, 900.0, 1200.0)


def _frame_at(frames: list, t: float) -> dict | None:
    best = None
    for row in frames:
        try:
            ft, parts = float(row[0]), row[1]
        except (TypeError, ValueError, IndexError):
            continue
        if ft <= t + 1.0 and isinstance(parts, dict):
            best = parts
    return best


def _tab_at(timeline: list, t: float) -> tuple[float, float, float] | None:
    """(Tab item-gold diff, CS diff, average level diff) from the record's scoreboard timeline."""
    best = None
    for row in timeline:
        try:
            gt = float(row[0])
        except (TypeError, ValueError, IndexError):
            continue
        if gt <= t + 1.0 and gt >= t - 90.0:
            lanes = row[2] if len(row) > 2 and isinstance(row[2], dict) else {}
            cs = sum(float(v[1] or 0) for v in lanes.values() if isinstance(v, list) and len(v) > 2)
            lv = sum(float(v[2] or 0) for v in lanes.values() if isinstance(v, list) and len(v) > 2) / 5.0
            best = (float(row[1] or 0), cs, lv)
    return best


def game_samples(record: dict, truth: dict) -> list[dict]:
    """One sample per checkpoint time: ``{t, win, exact: WinFactors, live: WinFactors | None}``."""
    res = str(record.get("result") or (record.get("summary") or {}).get("result") or "")
    if res not in ("Win", "Lose") or truth.get("queue_id") not in (400, 420, 430, 440, 490):
        return []
    win = res == "Win"
    mine = truth.get("my_team")
    team = {int(p["id"]): p.get("team") for p in truth.get("participants") or [] if isinstance(p, dict)}
    dur = float(truth.get("duration") or 0)
    out = []
    for t in TIMES:
        if dur < t + 30:
            continue
        fr = _frame_at(truth.get("frames") or [], t)
        if not fr:
            continue
        g = {"ORDER": 0.0, "CHAOS": 0.0}
        lv = {"ORDER": 0.0, "CHAOS": 0.0}
        cs = {"ORDER": 0.0, "CHAOS": 0.0}
        for pid, v in fr.items():
            tm = team.get(int(pid))
            if tm in g and isinstance(v, list) and len(v) >= 6:
                g[tm] += float(v[2]); cs[tm] += float(v[4]); lv[tm] += float(v[5]) / 5.0
        other = "CHAOS" if mine == "ORDER" else "ORDER"
        k = {"ORDER": 0, "CHAOS": 0}
        for row in truth.get("kills") or []:
            if float(row[0]) <= t and team.get(int(row[2])) in k:
                k[other if team.get(int(row[2])) == mine else mine] += 1
        tw = {"ORDER": 0, "CHAOS": 0}
        for row in truth.get("buildings") or []:
            if float(row[0]) <= t and row[3] == "TOWER_BUILDING" and row[2] in tw:
                tw[other if row[2] == mine else mine] += 1          # row[2] = the building's team
        dr = {"ORDER": 0, "CHAOS": 0}
        for row in truth.get("monsters") or []:
            if float(row[0]) <= t and row[3] == "DRAGON" and row[2] in dr:
                dr[row[2]] += 1                                       # row[2] = the killer's team
        kd, td, dd = k[mine] - k[other], tw[mine] - tw[other], dr[mine] - dr[other]
        common = dict(game_time=t, kill_diff=kd, tower_diff=td, dragon_diff=dd)
        exact = H.WinFactors(gold_diff=g[mine] - g[other], level_diff=lv[mine] - lv[other], **common)
        live = None
        tab = _tab_at(((record.get("scoreboard") or {}).get("timeline")) or [], t)
        if tab is not None:
            gold = H.gold_lead_estimate(kd, td, tab[1], tab[2], tab[0])
            live = H.WinFactors(gold_diff=gold, level_diff=tab[2], **common)
            tab_only = H.WinFactors(game_time=t, gold_diff=tab[0], kill_diff=kd)      # the pre-2.21 inputs
        else:
            tab_only = None
        raw = {"gold": g[mine] - g[other], "kd": kd, "td": td, "dd": dd,
               "tab": tab[0] if tab else None, "cs": tab[1] if tab else None, "lv": tab[2] if tab else None}
        out.append({"t": t, "win": win, "exact": exact, "live": live, "tab_only": tab_only, "raw": raw})
    return out


def load(games: Path, truth_dir: Path) -> list[list[dict]]:
    """Samples grouped by game (for leave-one-game-out)."""
    out = []
    for tf in sorted(truth_dir.glob("*.truth.json")):
        try:
            truth = json.loads(tf.read_text(encoding="utf-8"))
            rec = json.loads((games / str(truth.get("record") or "")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        s = game_samples(rec, truth)
        if s:
            out.append(s)
    return out


# ---------------------------------------------------------------------------------------- model
def prob(f: H.WinFactors, p: dict) -> float:
    """hype.win_probability with overridable weights ``p`` (scale ``k`` on the whole logit)."""
    g = (f.gold_diff or 0.0) / 1000.0
    x = (p["W_GOLD"] * g * H._gold_scale(f.game_time) + p["W_KILL"] * f.kill_diff + p["W_TOWER"] * f.tower_diff
         + p["W_DRAKE"] * f.dragon_diff + p["W_LEVEL"] * f.level_diff) * p.get("k", 1.0)
    return min(H.P_MAX, max(H.P_MIN, 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))))


def current() -> dict:
    return {"W_GOLD": H.W_GOLD, "W_KILL": H.W_KILL, "W_TOWER": H.W_TOWER, "W_DRAKE": H.W_DRAKE,
            "W_LEVEL": H.W_LEVEL, "k": 1.0}


def logloss(samples: list[dict], key: str, p: dict) -> float:
    s = [x for x in samples if x[key] is not None]
    if not s:
        return math.inf
    return -sum(math.log(prob(x[key], p) if x["win"] else 1.0 - prob(x[key], p)) for x in s) / len(s)


#: bounded searches: name -> {param: candidate values}; one or two parameters each
GRIDS = {
    "scale k":          {"k": [0.6 + 0.1 * i for i in range(15)]},                 # 0.6 .. 2.0
    "W_GOLD":           {"W_GOLD": [0.15 + 0.05 * i for i in range(12)]},          # 0.15 .. 0.70
    "W_GOLD + W_LEVEL": {"W_GOLD": [0.15 + 0.05 * i for i in range(12)],
                         "W_LEVEL": [0.0, 0.1, 0.2, 0.3, 0.45, 0.6]},
}


def _fit(samples: list[dict], key: str, grid: dict) -> dict:
    import itertools
    names = list(grid)
    best, best_ll = current(), math.inf
    for combo in itertools.product(*(grid[n] for n in names)):
        p = {**current(), **dict(zip(names, combo))}
        ll = logloss(samples, key, p)
        if ll < best_ll - 1e-9:
            best, best_ll = p, ll
    return best


def score(rows: list[tuple[dict, float]]) -> dict:
    """rows = (sample, predicted p) -> per-time Brier / accuracy."""
    out = {}
    for t in TIMES:
        r = [(s["win"], p) for s, p in rows if s["t"] == t]
        if r:
            out[t] = (sum((p - w) ** 2 for w, p in r) / len(r), sum((p >= 0.5) == w for w, p in r), len(r))
    return out


def evaluate(games: list[list[dict]], key: str) -> dict[str, dict]:
    res = {"current": score([(s, prob(s[key], current())) for g in games for s in g if s[key] is not None])}
    for name, grid in GRIDS.items():
        rows = []
        for i, g in enumerate(games):                                  # leave one GAME out
            train = [s for j, gg in enumerate(games) if j != i for s in gg]
            p = _fit(train, key, grid)
            rows += [(s, prob(s[key], p)) for s in g if s[key] is not None]
        full = _fit([s for g in games for s in g], key, grid)
        res[f"{name} (LOO)  fit on all: " + ", ".join(f"{n}={full[n]:.2f}" for n in grid)] = score(rows)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("games", type=Path)
    ap.add_argument("--truth", type=Path, default=None)
    a = ap.parse_args(argv)
    games = load(a.games, a.truth or a.games / "truth")
    print(f"{len(games)} games with LCU timeline and a result")
    global TIMES
    keep, TIMES = TIMES, tuple(60.0 * m for m in range(4, 36))
    err = [(s["live"].gold_diff - s["raw"]["gold"]) for g in load(a.games, a.truth or a.games / "truth")
           for s in g if s["live"] is not None]
    TIMES = keep
    if err:
        print(f"live gold-lead estimate vs exact: {math.sqrt(sum(e * e for e in err) / len(err)):.0f} RMS"
              f" over {len(err)} frames (4..35 min)")
    for key in ("exact", "live", "tab_only"):
        print(f"\n== inputs: {key}   (Brier / right / n at 10, 15, 20 min)")
        for name, sc in evaluate(games, key).items():
            cells = "   ".join(f"{sc[t][0]:.3f} {sc[t][1]:>2}/{sc[t][2]:<2}" if t in sc else "  -  " for t in TIMES)
            print(f"  {cells}   {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
