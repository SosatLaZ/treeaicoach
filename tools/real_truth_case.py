"""Real minimap captures + LCU truth -> a labelled detection case (the ``tests/fixtures/real``
format scored by ``tools/real_minimap_bench.py --dir`` and the gym's "REAL CASE" lines).

Inputs: the app's minimap captures of ONE game (``collect/YYYYMMDD_HHMMSS_NNNN.png``, the
file name is the local wall-clock time), the game record (``games/<stem>.json``: the
record's ``meta.start`` and ``my_positions``) and its LCU truth (``games/truth/<stem>.truth.json``:
per-minute positions of the 10 champions, kills with positions).

1. **Wall clock -> game time.** ``game_time = wall - meta.start - delta``: ``delta`` (the app
   started recording a little before 0:00, the file name is rounded to the second) is fitted
   by running the production detector on every capture and matching its detections with
   what the app recorded on the same frames (named sightings, allies, my positions): the
   delta with the smallest mean distance wins (sharp: same pixels, same detector)
   (``--offset`` to give it by hand, ``--cache`` keeps the detector pass between runs).
2. **Truth position of every champion at the capture time.** An anchor (minute frame, or a
   kill: the victim stands there) within ``NEAR_S`` of the capture, else a linear
   interpolation between the two bracketing minute frames when the champion barely moved
   (``INTERP_MOVE``), else unknown. Dead champions (kills + respawn timer, my own deaths from
   the record's Live Client snapshots) are listed in ``dead`` and never labelled.
3. **Snap to the icon.** The truth is rounded and a few seconds old: the label is put on the
   icon-like ring of the champion's team colour (``self_icon.ring_candidates``, no identity
   involved) that is alone within the reach of the truth position; a champion whose icon
   cannot be found is not labelled, and when the truth says he is on the minimap his truth
   position goes to ``unsure`` (the bench ignores detections there: neither hit nor false
   positive). The name is kept only when no other alive champion of the same team could
   stand there (``AMBIG_D``), otherwise the label is anonymous (position + team).
4. **Output.** ``ground_truth.json`` + the PNGs (copied). ``--keep N`` writes the N most
   useful frames (most labels, bot-lane stacks, anchored frames, spread over the game) to
   ``--out`` and ALL frames to ``--out-all`` (outside the repo). ``--preview DIR`` writes x2
   annotated frames for a visual check. Labels are sanitized: champion alias, team,
   position, dead list, self flag. Never a riot id or a summoner name.

    python tools/real_truth_case.py up/collect --record up/games/X.json --truth up/games/truth/X.truth.json \\
        --out tests/fixtures/real_cases/X --out-all /scratch/X_all --keep 40 --preview /scratch/X_prev
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

MAP_UNITS = 14870.0
MAX_SPEED = 425.0 / MAP_UNITS          # ~0.0286 / s
NEAR_S = 4.0                           # an anchor this close in time (the captures fall ~3.5 s
#                                        from the minute frames): reach = SNAP_BASE + speed x dt
INTERP_MOVE = 0.05                     # moved less than this over a minute: interpolated
SNAP_BASE = 0.03                       # icon radius (0.043) + truth rounding, at dt = 0
SNAP_MAX = 0.135
AMBIG_D = 0.15                         # another champion of the team (no anchor near) this close:
#                                        anonymous label
BRW = (10, 10, 12, 12, 14, 16, 20, 25, 28, 32.5, 35, 37.5, 40, 42.5, 45, 47.5, 50, 52.5)
DEAD_IN_S = 1.5
DEAD_OUT_FRAC = 0.7
ICON_SCALE = 0.086                     # icon diameter / minimap side (recalibrated in the record)
STACK_D = 0.05                         # two champions this close: a stack (frame selection)
FOUNTAIN = {"ORDER": (0.045, 0.955), "CHAOS": (0.955, 0.045)}


# ----------------------------------------------------------------------------- truth
class Truth:
    def __init__(self, truth: dict, record: dict | None = None) -> None:
        self.parts = {int(p["id"]): p for p in truth.get("participants", [])}
        self.me = int(truth.get("me") or 0)
        self.my_team = truth.get("my_team") or self.parts.get(self.me, {}).get("team")
        self.frames = [(float(t), {int(k): tuple(v) for k, v in fr.items()})
                       for t, fr in truth.get("frames", [])]
        self.frame_t = [t for t, _ in self.frames]
        self.kills = [(float(k[0]), int(k[2] or 0), float(k[4]), float(k[5]))
                      for k in truth.get("kills", []) if len(k) >= 6]
        self.dead: dict[int, list[tuple[float, float]]] = {}
        for t, victim, _u, _v in self.kills:
            lvl = self.level(victim, t)
            timer = BRW[max(0, min(len(BRW) - 1, lvl - 1))]
            self.dead.setdefault(victim, []).append((t + DEAD_IN_S, t + DEAD_OUT_FRAC * timer))
        if record is not None and self.me:
            snaps = sorted((float(x.get("game_time") or 0.0), bool(x.get("is_dead")))
                           for x in record.get("snapshots") or [] if isinstance(x, dict))
            iv, start = [], None
            kills_me = [t for t, v, _u, _v in self.kills if v == self.me]
            for t, d in snaps:
                if d and start is None:
                    prev = [k for k in kills_me if t - 15.0 <= k <= t]
                    start = (prev[-1] if prev else t) + DEAD_IN_S
                elif not d and start is not None:
                    iv.append((start, t - 1.0))
                    start = None
            if iv:
                self.dead[self.me] = iv
        self.anchors: dict[int, list[tuple[float, float, float]]] = {pid: [] for pid in self.parts}
        for t, fr in self.frames:
            for pid, val in fr.items():
                if pid in self.anchors and not self.is_dead(pid, t):
                    self.anchors[pid].append((t, float(val[0]), float(val[1])))
        for t, victim, u, v in self.kills:
            if victim in self.anchors:
                self.anchors[victim].append((t, u, v))
        for a in self.anchors.values():
            a.sort()

    def team(self, pid: int) -> str:
        return str(self.parts[pid].get("team"))

    def side(self, pid: int) -> str:
        return "ally" if self.team(pid) == self.my_team else "enemy"

    def alias(self, pid: int) -> str:
        return str(self.parts[pid].get("alias"))

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

    def estimate(self, pid: int, t: float) -> tuple[float, float, float, str] | None:
        """(u, v, reach, how): the champion's position at ``t`` and how far the icon may be
        from it, or None when unknown."""
        best = None
        for at, u, v in self.anchors.get(pid, ()):
            if abs(at - t) <= NEAR_S and (best is None or abs(at - t) < abs(best[0] - t)):
                best = (at, u, v)
        if best is not None:
            dt = abs(best[0] - t)
            return best[1], best[2], min(SNAP_MAX, SNAP_BASE + MAX_SPEED * dt), "anchor"
        i = bisect.bisect_right(self.frame_t, t) - 1
        if 0 <= i < len(self.frames) - 1:
            t0, f0 = self.frames[i]
            t1, f1 = self.frames[i + 1]
            p0, p1 = f0.get(pid), f1.get(pid)
            if p0 is not None and p1 is not None and not self.is_dead(pid, t0) and \
                    not self.is_dead(pid, t1) and not self.is_dead(pid, t):
                moved = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
                if moved <= INTERP_MOVE:
                    k = (t - t0) / max(t1 - t0, 1e-6)
                    u = p0[0] + k * (p1[0] - p0[0])
                    v = p0[1] + k * (p1[1] - p0[1])
                    return u, v, min(SNAP_MAX, SNAP_BASE + 0.6 * moved), "interp"
                # a kill of the champion in the minute splits it (the respawn anchor is
                # the fountain): not interpolated
        return None

    def rough(self, pid: int, t: float) -> tuple[float, float] | None:
        """Linear interpolation between the bracketing frames (ambiguity check only)."""
        i = bisect.bisect_right(self.frame_t, t) - 1
        if 0 <= i < len(self.frames) - 1:
            t0, f0 = self.frames[i]
            t1, f1 = self.frames[i + 1]
            p0, p1 = f0.get(pid), f1.get(pid)
            if p0 is not None and p1 is not None:
                k = (t - t0) / max(t1 - t0, 1e-6)
                return p0[0] + k * (p1[0] - p0[0]), p0[1] + k * (p1[1] - p0[1])
        elif 0 <= i < len(self.frames):
            p = self.frames[i][1].get(pid)
            return (p[0], p[1]) if p is not None else None
        return None


# ----------------------------------------------------------------------------- wall clock
_NAME_RE = re.compile(r"(\d{8})_(\d{6})")


def wall_time(name: str, tz: Any) -> datetime | None:
    m = _NAME_RE.search(name)
    if not m:
        return None
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=tz)


def roster_of(T: Truth) -> dict:
    return {"self": T.alias(T.me),
            "ally": [T.alias(p) for p in T.parts if T.side(p) == "ally" and p != T.me],
            "enemy": [T.alias(p) for p in T.parts if T.side(p) == "enemy"]}


def detector_pass(caps: list[Path], game: dict, frames: int = 6, cache: Path | None = None,
                  verbose: bool = False) -> dict[str, list[dict]]:
    """Production detector (static replay) on every capture: {name: [detections]}."""
    if cache is not None and cache.is_file():
        try:
            d = json.loads(cache.read_text(encoding="utf-8"))
            if all(c.name in d for c in caps):
                return d
        except (OSError, ValueError):
            pass
    import cv2

    import real_minimap_bench as RB
    from treeaicoach.champions import get_default_db
    from treeaicoach.detector import create_detector

    db = get_default_db()
    out: dict[str, list[dict]] = {}
    for i, png in enumerate(caps):
        img = cv2.imread(str(png))
        det = create_detector("auto", db=db, learn_cache=None)
        det.matcher.set_entries(RB.roster_entries(game, db))
        dets = []
        for f in range(frames):
            dets = RB._detect(det, img, f / RB.FPS)
        out[png.name] = [{"u": round(d.u, 4), "v": round(d.v, 4), "cls": d.cls, "alias": d.alias,
                          "score": round(float(d.score), 3)} for d in dets]
        if verbose and i % 20 == 0:
            print(f"  detector pass {i}/{len(caps)}", flush=True)
    if cache is not None:
        cache.write_text(json.dumps(out), encoding="utf-8")
    return out


def fit_offset(caps: list[Path], walls: dict[str, datetime], start: datetime, dets: dict,
               record: dict, lo: float = -5.0, hi: float = 60.0,
               step: float = 0.1) -> tuple[float, float, int]:
    """delta (s) minimizing the mean distance between the detections of the capture (the
    production detector, static replay) and what the app itself recorded on the same frames
    at ``wall - start - delta`` (named ``sightings`` ~2 Hz, ``allies``, ``my_positions``);
    returns (delta, mean distance, samples). Same pixels, same detector: the curve is sharp
    (a fit on the truth frames alone would be limited by the champions' moves)."""
    series: dict[str, tuple[list, list]] = {}
    for src in (record.get("sightings") or {}, record.get("allies") or {}):
        for key, raw in src.items():
            s = sorted((float(x[0]), float(x[1]), float(x[2])) for x in raw or []
                       if isinstance(x, (list, tuple)) and len(x) >= 3)
            if s:
                series[key] = (s, [x[0] for x in s])
    me = str((record.get("meta") or {}).get("champion") or "")
    s = sorted((float(x[0]), float(x[1]), float(x[2])) for x in record.get("my_positions") or []
               if isinstance(x, (list, tuple)) and len(x) >= 3)
    if me and s:
        series[me] = (s, [x[0] for x in s])
    pts = []
    for c in caps:
        w = walls.get(c.name)
        if w is None:
            continue
        tw = (w - start).total_seconds()
        for d in dets.get(c.name, []):
            if d.get("alias") in series:
                pts.append((tw, d["alias"], d["u"], d["v"]))

    def err(t: float, alias: str, u: float, v: float, win: float = 0.6) -> float | None:
        ss, ts = series[alias]
        i = bisect.bisect_left(ts, t)
        a = ss[i - 1] if 0 < i <= len(ss) and abs(ts[i - 1] - t) <= win else None
        b = ss[i] if 0 <= i < len(ss) and abs(ts[i] - t) <= win else None
        if a is not None and b is not None and b[0] > a[0]:
            k = (t - a[0]) / (b[0] - a[0])            # between two samples: interpolated
            pu, pv = a[1] + k * (b[1] - a[1]), a[2] + k * (b[2] - a[2])
        elif a is not None or b is not None:
            pu, pv = (a or b)[1], (a or b)[2]
        else:
            return None
        return min(math.hypot(pu - u, pv - v), 0.1)

    best = (0.0, 9.0, 0)
    d = lo
    while d <= hi + 1e-9:
        errs = [e for e in (err(tw - d, a, u, v) for tw, a, u, v in pts) if e is not None]
        if len(errs) >= max(5, len(pts) // 4):
            m = float(np.mean(errs))
            if m < best[1]:
                best = (round(d, 1), m, len(errs))
        d += step
    return best


# ----------------------------------------------------------------------------- labels
_GENERIC = None


def icon_candidates(img: np.ndarray) -> list:
    """Icon-like spots with a team side, no identity: the ring finder of self_icon.py and the
    generic (roster-free) detector, merged (one icon drawn over another gives one spot)."""
    from treeaicoach.detector import create_detector
    from treeaicoach.self_icon import RingCand, ring_candidates

    global _GENERIC
    H, W = img.shape[:2]
    R_px = 0.5 * ICON_SCALE * W
    cands = list(ring_candidates(img, R_px, fountains=False))
    if _GENERIC is None:
        _GENERIC = create_detector("auto", roster=False)
    for d in _GENERIC.detect(img) or []:
        side = "enemy" if d.cls == "enemy" else "ally"
        if not any(c.side == side and math.hypot(c.u - d.u, c.v - d.v) < 0.5 * ICON_SCALE for c in cands):
            cands.append(RingCand(float(d.u), float(d.v), side, float(d.score)))
    return cands


def label_frame(img: np.ndarray, T: Truth, t: float) -> dict:
    cands = icon_candidates(img)
    dead = [T.alias(p) for p in T.parts if T.is_dead(p, t)]
    alive = [p for p in T.parts if not T.is_dead(p, t)]
    est = {p: T.estimate(p, t) for p in alive}
    rough = {p: T.rough(p, t) for p in alive}
    champs, unsure = [], []
    taken: dict[int, list[int]] = {}
    for p in alive:
        e = est[p]
        if e is None:
            continue
        u, v, reach, how = e
        side = T.side(p)
        near = [i for i, c in enumerate(cands)
                if c.side == side and math.hypot(c.u - u, c.v - v) <= reach]
        if len(near) != 1:
            # not found alone within reach (in the fog, under another icon, missed by the icon
            # finders): whatever is detected within reach is not judged (neither hit nor false)
            unsure.append({"name": T.alias(p), "team": side, "u": round(u, 4), "v": round(v, 4),
                           "r": round(reach, 3), "why": "no icon" if not near else f"{len(near)} icons"})
            continue
        c = cands[near[0]]
        others = []
        for q in alive:
            if q == p or T.side(q) != side:
                continue
            eq = est[q]
            if eq is not None and eq[3] == "anchor":
                # an anchor near in time: the champion is within its reach, or not there
                if math.hypot(eq[0] - c.u, eq[1] - c.v) <= eq[2]:
                    others.append(q)
            elif rough[q] is not None and math.hypot(rough[q][0] - c.u, rough[q][1] - c.v) <= AMBIG_D:
                others.append(q)
        rec = {"u": round(c.u, 4), "v": round(c.v, 4), "team": side,
               "name": None if others else T.alias(p), "how": how}
        if others:
            rec["could_be"] = sorted(T.alias(q) for q in others) + [T.alias(p)]
        if p == T.me:
            rec["self"] = True
        taken.setdefault(near[0], []).append(len(champs))
        champs.append(rec)
    # two champions snapped to one icon: a stack (both kept: the detector must report both);
    # when the only other champions who could stand there are the stack's own members, the
    # names are known (a bot-lane duo: the support on the ADC)
    for idxs in taken.values():
        if len(idxs) > 1:
            names = {champs[k].get("name") or champs[k].get("could_be", [None])[-1] for k in idxs}
            for k in idxs:
                champs[k]["stacked"] = True
                cb = champs[k].get("could_be")
                if cb and set(cb) <= names:
                    champs[k]["name"] = cb[-1]
    # false positives of a side are judged only when every alive champion of that side has a
    # truth estimate (else an unknown champion may stand anywhere)
    judged = {side: all(est[p] is not None for p in alive if T.side(p) == side) for side in ("ally", "enemy")}
    return {"champions": champs, "unsure": unsure, "dead": dead, "fp_judged": judged}


def frame_value(spec: dict) -> float:
    ch = spec["champions"]
    val = float(len(ch)) + 0.5 * sum(1 for c in ch if c.get("name"))
    val += 2.0 * sum(1 for c in ch if c.get("stacked")) / 2.0
    val += 1.5 * sum(1 for c in ch if c.get("how") == "anchor") / max(1, len(ch))
    # bot-lane duo: me (support) next to an ally within one icon diameter
    me = [c for c in ch if c.get("self")]
    if me and any(not c.get("self") and c["team"] == "ally" and
                  math.hypot(c["u"] - me[0]["u"], c["v"] - me[0]["v"]) <= STACK_D * 2 for c in ch):
        val += 2.0
    val -= 0.5 * len(spec["unsure"])
    return val


def choose(names: list[str], specs: dict, keep: int) -> list[str]:
    """The ``keep`` most useful frames, spread over the game (one per time slice)."""
    names = [n for n in names if specs[n]["champions"]]
    if keep <= 0 or keep >= len(names):
        return list(names)
    slices = max(1, keep)
    per = len(names) / slices
    chosen = []
    for s in range(slices):
        seg = names[int(s * per):int((s + 1) * per)]
        if seg:
            chosen.append(max(seg, key=lambda n: frame_value(specs[n])))
    return chosen[:keep]


def preview(img: np.ndarray, spec: dict, path: Path) -> None:
    import cv2

    z = 2
    big = cv2.resize(img, None, fx=z, fy=z, interpolation=cv2.INTER_CUBIC)
    H, W = big.shape[:2]
    for c in spec["champions"]:
        x, y = int(c["u"] * W), int(c["v"] * H)
        col = (0, 200, 255) if c["team"] == "enemy" else (255, 200, 0)
        cv2.circle(big, (x, y), int(0.5 * ICON_SCALE * W) + 3, col, 2)
        txt = (c.get("name") or "?") + ("*" if c.get("self") else "") + ("+" if c.get("stacked") else "")
        cv2.putText(big, txt, (x + 12, y - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3)
        cv2.putText(big, txt, (x + 12, y - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    for c in spec["unsure"]:
        x, y = int(c["u"] * W), int(c["v"] * H)
        cv2.drawMarker(big, (x, y), (0, 0, 255), cv2.MARKER_TILTED_CROSS, 18, 2)
        cv2.putText(big, c["name"] + "?", (x + 10, y + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
    cv2.putText(big, f"t={spec['time']:.1f}s dead={','.join(spec['dead']) or '-'}", (6, H - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.imwrite(str(path), big)


def build(captures: Path, record: dict, truth: dict, out: Path, out_all: Path | None = None,
          keep: int = 40, offset: float | None = None, cache: Path | None = None,
          preview_dir: Path | None = None, frames: int = 6, verbose: bool = False) -> dict:
    import cv2

    T = Truth(truth, record)
    game = roster_of(T)
    meta = record.get("meta") or {}
    start = datetime.fromisoformat(str(meta.get("start")))
    start -= timedelta(seconds=float(meta.get("start_game_time") or 0.0))
    caps = sorted(p for p in Path(captures).glob("*.png") if _NAME_RE.search(p.name))
    walls = {c.name: wall_time(c.name, start.tzinfo) for c in caps}
    stats: dict[str, Any] = {"captures": len(caps)}
    if offset is None:
        dets = detector_pass(caps, game, frames, cache, verbose)
        offset, med, n = fit_offset(caps, walls, start, dets, record)
        stats.update({"offset_fit_mean": round(med, 4), "offset_fit_n": n})
    stats["offset"] = offset
    duration = float(truth.get("duration") or record.get("duration") or 1e9)
    specs: dict[str, dict] = {}
    images: dict[str, np.ndarray] = {}
    for c in caps:
        t = (walls[c.name] - start).total_seconds() - offset
        if t < 0 or t > duration:
            continue
        img = cv2.imread(str(c))
        if img is None:
            continue
        spec = label_frame(img, T, t)
        spec["time"] = round(t, 1)
        name = c.stem
        specs[name] = spec
        images[name] = img
    names = sorted(specs)
    kept = choose(names, specs, keep)
    gkey = "g1"
    about = (f"Real minimap captures of the user's {game['self']} game (x{len(names)} every ~10 s, "
             f"{meta.get('app_version')}; the overlay is drawn in the capture) labelled from the LCU truth "
             f"(tools/real_truth_case.py: wall clock -> game time offset {offset} s, positions from the "
             f"minute frames / kills snapped to the icon ring of the champion's team, anonymous when "
             f"another champion of the team could stand there; 'unsure' = on the minimap per the truth but "
             f"no icon found: ignored by the bench; 'dead' from the kills + respawn timer / my snapshots).")

    def write(folder: Path, sel: list[str]) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        gt = {"about": about, "rosters": {gkey: game}, "images": {}}
        for n in sel:
            sp = specs[n]
            fn = f"{n}.png"
            if not (folder / fn).is_file():
                shutil.copy(captures / fn, folder / fn)
            champs = [{k: v for k, v in c.items() if k not in ("how", "could_be")} for c in sp["champions"]]
            gt["images"][n] = {"game": gkey, "file": fn, "time": sp["time"], "dead": sp["dead"],
                               "champions": champs, "unsure": sp["unsure"], "fp_judged": sp["fp_judged"]}
        (folder / "ground_truth.json").write_text(json.dumps(gt, indent=1), encoding="utf-8")

    write(out, kept)
    if out_all is not None:
        write(out_all, names)
    if preview_dir is not None:
        preview_dir.mkdir(parents=True, exist_ok=True)
        for n in names:
            preview(images[n], specs[n], preview_dir / f"{n}.png")
    labels = sum(len(s["champions"]) for s in specs.values())
    stats.update({"frames": len(names), "kept": len(kept), "labels": labels,
                  "named": sum(1 for s in specs.values() for c in s["champions"] if c.get("name")),
                  "stacked": sum(1 for s in specs.values() for c in s["champions"] if c.get("stacked")),
                  "anchor": sum(1 for s in specs.values() for c in s["champions"] if c.get("how") == "anchor"),
                  "unsure": sum(len(s["unsure"]) for s in specs.values()),
                  "kept_labels": sum(len(specs[n]["champions"]) for n in kept)})
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("captures")
    ap.add_argument("--record", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--out", required=True, help="case folder (the kept frames)")
    ap.add_argument("--out-all", help="case folder with every frame (outside the repo)")
    ap.add_argument("--keep", type=int, default=40)
    ap.add_argument("--offset", type=float, help="wall - start - game_time (s); fitted when absent")
    ap.add_argument("--cache", help="JSON cache of the detector pass (offset fit)")
    ap.add_argument("--frames", type=int, default=6)
    ap.add_argument("--preview", help="folder for x2 annotated frames")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    record = json.loads(Path(a.record).read_text(encoding="utf-8"))
    truth = json.loads(Path(a.truth).read_text(encoding="utf-8"))
    st = build(Path(a.captures), record, truth, Path(a.out), Path(a.out_all) if a.out_all else None,
               a.keep, a.offset, Path(a.cache) if a.cache else None,
               Path(a.preview) if a.preview else None, a.frames, a.v)
    print(json.dumps(st))


if __name__ == "__main__":
    main()
