"""Version bisect of the detection on REAL data with the SHIPPED configuration.

For every git revision given, a worktree is created in ``--work`` (default: the scratchpad
``bisect`` folder) and a worker process runs THAT version's own engine (default ``Config()``,
its own ONNX model / verifier / det_params, its performance profile) through a FrameSource on:

* the labelled real crops of THIS checkout (``tests/fixtures/real``: ground truth), each fed
  ``--frames`` times at ``--fps`` (a static scene, like the real bench but through
  ``CoachEngine.step``: detector + identifier + stabilize + tracker);
* full in-game screenshots (``--shots``): the version's own minimap locator first, then the
  same pipeline (the locator rectangle and the detection count are reported).

Per version: recall / precision / identity of the tracker's VISIBLE tracks vs the ground
truth, ghosts (overlay views drawn as unsure on a labelled champion), what the overlay would
draw by default (compact mode: rings only on approaching visible enemies; last-seen marks),
and the per-frame cost.

    python tools/det_bisect.py deaa579 ed66350 7cb42be HEAD [--shots DIR] [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "real"
WORK = Path(os.environ.get("BISECT_WORK", "/tmp/det_bisect"))
TOL = 0.03


# ======================================================================================
# worker (runs inside the version under test)
# ======================================================================================

def _kw(cls, **kw):
    import dataclasses

    names = {f.name for f in dataclasses.fields(cls)} if dataclasses.is_dataclass(cls) else set(kw)
    return cls(**{k: v for k, v in kw.items() if k in names})


def _game(roster: dict, dead: list, t: float, db, filler: list):
    from treeaicoach.live_client import GameInfo, PlayerInfo

    allies = list(roster.get("ally", []))
    enemies = list(roster.get("enemy", []))
    k = 0
    while len(allies) < 4:
        allies.append(filler[k]); k += 1
    while len(enemies) < 5:
        enemies.append(filler[k]); k += 1
    my_team = roster.get("my_team", "CHAOS")
    other = "ORDER" if my_team == "CHAOS" else "CHAOS"

    def P(a, team, rid):
        return _kw(PlayerInfo, riot_id=rid, summoner_name=rid, champion_alias=a, champion_name=a, team=team,
                   position="", is_dead=a in dead, respawn_timer=20.0 if a in dead else 0.0, level=10)

    me = P(roster["self"], my_team, "Moi#BIS")
    return _kw(GameInfo, game_time=900.0 + t, game_mode="CLASSIC", map_number=11, map_terrain="Default",
               team_relative_colors=True, me=me, allies=[P(a, my_team, f"{a}#B") for a in allies],
               enemies=[P(a, other, f"{a}#B") for a in enemies], events=[], fetched_at=float(t),
               current_gold=500.0)


class _Voice:
    def __getattr__(self, item):
        return lambda *a, **k: None


def _run_case(img, roster, dead, gts, frames, fps, db, filler):
    from treeaicoach.config import Config
    from treeaicoach.engine import CoachEngine

    clock = [0.0]

    class Src:
        is_demo = False

        def next(self, t):
            return img, _game(roster, dead, t, db, filler)

    eng = CoachEngine(Config(), _Voice(), frame_source=Src(), clock=lambda: clock[0],
                      enable_hotkeys=False, manage_overlay=False)
    ms = []
    for f in range(frames):
        clock[0] = f / fps
        t0 = time.perf_counter()
        eng.step(clock[0])
        ms.append((time.perf_counter() - t0) * 1000.0)
    t = clock[0]
    trk = eng._tracker
    tracks = [tr for tr in trk.tracks() if tr.visible and tr.position() is not None]
    out = {"ms_med": float(sorted(ms)[len(ms) // 2]), "tracks": [], "drawn": {}}
    for tr in tracks:
        p = tr.position()
        out["tracks"].append({"alias": tr.alias, "rel": tr.relation, "u": p[0], "v": p[1],
                              "stacked": getattr(tr, "stacked_with", None) is not None})
    # what the overlay would draw (default config)
    try:
        st = eng._build_overlay_state(t)
        from treeaicoach import overlay_render as OR

        is_ghost = getattr(OR, "is_ghost", lambda v: False)
        detailed = bool(getattr(st, "hud_detailed", False))
        live, ghost, rings, lastseen = 0, 0, 0, 0
        for v in list(st.enemies) + list(getattr(st, "allies", []) or []):
            if getattr(v, "visible", False) and v.uv is not None:
                if is_ghost(v) or getattr(v, "stacked", False):
                    ghost += 1
                else:
                    live += 1
                    if getattr(v, "relation", "enemy") == "enemy" and (detailed or getattr(v, "approaching", False)):
                        rings += 1
            elif getattr(v, "relation", "enemy") == "enemy" and v.uv is not None and not getattr(v, "dead", False):
                lastseen += 1
        out["drawn"] = {"live_views": live, "unsure_views": ghost, "enemy_rings_drawn": rings,
                        "lastseen_marks": lastseen, "me": st.me_uv is not None, "detailed": detailed}
    except Exception as exc:                                       # pragma: no cover - old versions
        out["drawn"] = {"error": repr(exc)}
    # score against the ground truth
    if gts is not None:
        used = set()
        tp = idok = idn = 0
        for g in gts:
            best = None
            for k, tr in enumerate(out["tracks"]):
                if k in used:
                    continue
                d = math.hypot(tr["u"] - g["u"], tr["v"] - g["v"])
                if d < TOL and (best is None or d < best[0]):
                    best = (d, k)
            if best is not None:
                used.add(best[1])
                tp += 1
                if g.get("name"):
                    idn += 1
                    tr = out["tracks"][best[1]]
                    idok += tr["alias"] == g["name"] or (tr["rel"] == "self" and g["name"] == roster["self"])
        out.update(gt=len(gts), tp=tp, n=len(out["tracks"]), idn=idn, idok=idok)
    return out


def worker(args) -> None:
    import cv2
    import logging

    logging.disable(logging.WARNING)
    from treeaicoach.champions import get_default_db

    db = get_default_db()
    truth = json.loads((FIX / "ground_truth.json").read_text(encoding="utf-8"))
    names = sorted(e.alias for e in db.all() if db.load_icon(e.alias) is not None)
    res = {"version": getattr(__import__("treeaicoach"), "__version__", "?"), "crops": {}, "shots": {}}
    for name, spec in truth["images"].items():
        img = cv2.imread(str(FIX / spec["file"]))
        ro = dict(truth["rosters"][spec["game"]])
        ro.setdefault("my_team", "CHAOS")
        used = {ro["self"], *ro.get("ally", []), *ro.get("enemy", [])}
        filler = [a for a in names if a not in used][:10]
        res["crops"][name] = _run_case(img, ro, spec.get("dead", []), spec["champions"], args.frames,
                                       args.fps, db, filler)
    if args.shots:
        from treeaicoach.minimap_locator import MinimapLocator

        try:
            from treeaicoach.capture import Rect
        except Exception:                                          # pragma: no cover
            Rect = None
        loc = MinimapLocator()
        ro = {"self": "Garen", "ally": ["MasterYi", "Mel", "Ezreal", "Brand"],
              "enemy": ["Vladimir", "Kindred", "Ekko", "Seraphine", "Thresh"], "my_team": "CHAOS"}
        for p in sorted(Path(args.shots).glob("*.png")):
            scr = cv2.imread(str(p))
            if scr is None:
                continue
            h, w = scr.shape[:2]
            t0 = time.perf_counter()
            try:
                L = loc.locate(scr, Rect(0, 0, w, h) if Rect else None)
            except TypeError:
                L = loc.locate(scr)
            lms = (time.perf_counter() - t0) * 1000.0
            if L is None:
                res["shots"][p.name] = {"located": False}
                continue
            r = L.rect
            crop = scr[r.y:r.y + r.h, r.x:r.x + r.w]
            filler = [a for a in names if a not in {ro['self'], *ro['ally'], *ro['enemy']}][:10]
            o = _run_case(crop, ro, [], None, args.frames, args.fps, db, filler)
            res["shots"][p.name] = {"located": True, "rect": [r.x, r.y, r.w, r.h], "score": round(L.score, 3),
                                    "locate_ms": round(lms, 1), "n_tracks": len(o["tracks"]),
                                    "named": sum(1 for t in o["tracks"] if t["alias"]), "drawn": o["drawn"],
                                    "ms_med": o["ms_med"]}
    print("JSON:" + json.dumps(res))


# ======================================================================================
# driver
# ======================================================================================

def ensure_worktree(rev: str) -> Path:
    if rev in ("HEAD", "WORKTREE", "."):
        return ROOT
    d = WORK / rev
    if not (d / "treeaicoach").is_dir():
        WORK.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "worktree", "add", "--detach", str(d), rev], cwd=ROOT, check=True,
                       capture_output=True)
    return d


def summarize(res: dict) -> dict:
    gt = sum(c.get("gt", 0) for c in res["crops"].values())
    tp = sum(c.get("tp", 0) for c in res["crops"].values())
    n = sum(c.get("n", 0) for c in res["crops"].values())
    idn = sum(c.get("idn", 0) for c in res["crops"].values())
    idok = sum(c.get("idok", 0) for c in res["crops"].values())
    ms = [c["ms_med"] for c in res["crops"].values()]
    dr = [c["drawn"] for c in res["crops"].values() if "error" not in c["drawn"]]
    return {"version": res["version"], "recall": tp / max(1, gt), "precision": tp / max(1, n),
            "identity": idok / max(1, idn), "gt": gt, "tp": tp, "tracks": n,
            "step_ms_med": float(sorted(ms)[len(ms) // 2]) if ms else None,
            "rings_drawn": sum(d.get("enemy_rings_drawn", 0) for d in dr),
            "live_views": sum(d.get("live_views", 0) for d in dr),
            "unsure_views": sum(d.get("unsure_views", 0) for d in dr),
            "lastseen_marks": sum(d.get("lastseen_marks", 0) for d in dr)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("revs", nargs="*", default=["HEAD"])
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--fps", type=float, default=6.0)
    ap.add_argument("--shots")
    ap.add_argument("--json")
    ap.add_argument("--worker", action="store_true")
    ap.add_argument("--pkg")
    a = ap.parse_args()
    if a.worker:
        sys.path.insert(0, a.pkg)
        worker(a)
        return
    allres = {}
    for rev in a.revs:
        pkg = ensure_worktree(rev)
        cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", "--pkg", str(pkg),
               "--frames", str(a.frames), "--fps", str(a.fps)] + (["--shots", a.shots] if a.shots else [])
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(pkg))
        line = [ln for ln in p.stdout.splitlines() if ln.startswith("JSON:")]
        if not line:
            print(f"{rev}: worker failed\n{p.stderr[-2000:]}")
            continue
        res = json.loads(line[0][5:])
        allres[rev] = res
        s = summarize(res)
        print(f"{rev:9s} v{s['version']:7s} recall {s['recall']:.3f} precision {s['precision']:.3f} "
              f"identity {s['identity']:.3f} ({s['tp']}/{s['gt']}, {s['tracks']} tracks)  step {s['step_ms_med']:.1f} ms  "
              f"overlay: live {s['live_views']} unsure {s['unsure_views']} rings {s['rings_drawn']} "
              f"last-seen {s['lastseen_marks']}", flush=True)
        for nm, c in res["crops"].items():
            print(f"    {nm:8s} {c.get('tp')}/{c.get('gt')} tracks {c.get('n')} id {c.get('idok')}/{c.get('idn')} "
                  f"drawn {c['drawn']}")
        for nm, c in res["shots"].items():
            print(f"    shot {nm}: {c}")
    if a.json:
        Path(a.json).write_text(json.dumps(allres, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
