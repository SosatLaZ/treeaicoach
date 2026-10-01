"""Turn a user diagnostic bundle (``diag_*.zip`` from the app, see treeaicoach/diag.py) into a
permanent real-data test case for the detection gym / real-crop bench.

Every sample of the bundle has the raw minimap crop (``frames/NNN_minimap.png``) and what the
app saw (``frames/NNN.json``: detections with identity scores, tracks, the Live Client roster
with the dead flags). The converter writes a case folder in the ``tests/fixtures/real`` format
(``ground_truth.json`` + PNGs) with PSEUDO-labels:

* a detection identified with ``id_score >= --min-id`` (default 0.85) becomes a label
  (champion, team from the roster, centre);
* low-confidence or unidentified detections, visible tracks without a detection (coasting /
  stacked) and detections of champions the Live Client says are dead go to ``needs_check``
  (listed per image, NOT scored) - a human fixes them once (move them to ``champions``,
  correct the name, or delete them) and the case is then exact;
* ``--positions`` (optional) cross-checks the labels with known positions, e.g. the League
  Client match timeline (per-minute participant positions): JSON
  ``{"positions": {"<game time s>": {"<alias>": [x, y]}}}`` in game units; a label farther
  than ``--check-dist`` (normalized) from the known position at the nearest time (within
  2 s) moves to ``needs_check``.

The real bench (``tools/real_minimap_bench.py --dir <case>``) and the gym
(``det_gym.py`` scores every folder under ``tests/fixtures/real_cases``) then use it, so each
bug report becomes a regression test.

    python tools/diag_to_gym.py diag_20261001_201500.zip [--out tests/fixtures/real_cases/<name>]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CASES_DIR = ROOT / "tests" / "fixtures" / "real_cases"


def _open(src: Path) -> tuple[Path, Path | None]:
    if src.is_dir():
        return src, None
    tmp = Path(tempfile.mkdtemp(prefix="diag2gym_"))
    with zipfile.ZipFile(src) as zf:
        zf.extractall(tmp)
    subs = [p for p in tmp.iterdir() if p.is_dir()]
    root = subs[0] if len(subs) == 1 and not (tmp / "frames").is_dir() else tmp
    return root, tmp


def _roster(game: dict) -> dict:
    me = (game.get("me") or {}).get("champion")
    my_team = game.get("my_team")
    out = {"self": me, "ally": [], "enemy": []}
    for p in game.get("players", []):
        a = p.get("champion")
        if not a or a == me:
            continue
        out["ally" if p.get("team") == my_team else "enemy"].append(a)
    return out


def convert(src: Path, out: Path, min_id: float = 0.85, positions: dict | None = None,
            check_dist: float = 0.08) -> dict:
    from treeaicoach.geometry import game_to_uv

    root, tmp = _open(Path(src))
    try:
        frames = sorted((root / "frames").glob("*_minimap.png"))
        out.mkdir(parents=True, exist_ok=True)
        gt: dict = {"about": f"pseudo-labels from {Path(src).name} (tools/diag_to_gym.py): 'champions' "
                             "are high-confidence detections, 'needs_check' must be reviewed by a human",
                    "pseudo": True, "rosters": {}, "images": {}}
        stats = {"images": 0, "labels": 0, "needs_check": 0}
        pos_t = {float(k): v for k, v in (positions or {}).items()}
        for png in frames:
            n = png.name.split("_")[0]
            js = root / "frames" / f"{n}.json"
            if not js.is_file():
                continue
            snap = json.loads(js.read_text(encoding="utf-8"))
            game = snap.get("game") or {}
            if not game.get("players"):
                continue
            ro = _roster(game)
            key = "g" + str(abs(hash((ro["self"], tuple(sorted(ro["ally"])), tuple(sorted(ro["enemy"]))))) % 10 ** 6)
            gt["rosters"][key] = ro
            my_team = game.get("my_team")
            team_of = {p["champion"]: ("ally" if p.get("team") == my_team else "enemy")
                       for p in game.get("players", []) if p.get("champion")}
            dead = [p["champion"] for p in game.get("players", []) if p.get("dead") and p.get("champion")]
            champs, check = [], []
            seen = set()
            for d in snap.get("detections", []):
                a, u, v = d.get("alias"), d.get("u"), d.get("v")
                if u is None or v is None:
                    continue
                rec = {"name": a, "team": team_of.get(a) or ("enemy" if d.get("relation") == "enemy" else "ally"),
                       "u": round(float(u), 4), "v": round(float(v), 4)}
                why = None
                if not a:
                    why = "unidentified"
                elif a in dead:
                    why = "dead per Live Client"
                elif float(d.get("id_score") or 0.0) < min_id:
                    why = f"low identity score {float(d.get('id_score') or 0.0):.2f}"
                elif pos_t:
                    gtime = float(game.get("game_time") or 0.0)
                    tk = min(pos_t, key=lambda k: abs(k - gtime))
                    known = pos_t[tk].get(a) if abs(tk - gtime) <= 2.0 else None
                    if known is not None:
                        ku, kv = game_to_uv(*known)
                        if ((ku - u) ** 2 + (kv - v) ** 2) ** 0.5 > check_dist:
                            why = "disagrees with the known position"
                if why is None:
                    champs.append(rec)
                    seen.add(a)
                else:
                    check.append(dict(rec, why=why))
            for tr in snap.get("tracks", []):
                if tr.get("visible") and tr.get("alias") and tr["alias"] not in seen and tr.get("pos"):
                    check.append({"name": tr["alias"], "team": team_of.get(tr["alias"], "ally"),
                                  "u": tr["pos"][0], "v": tr["pos"][1],
                                  "why": "track visible without a detection (coasting / stacked)"})
            name = f"{out.name}_{n}"
            shutil.copy(png, out / f"{name}.png")
            gt["images"][name] = {"game": key, "file": f"{name}.png", "dead": dead,
                                  "time": game.get("game_time"), "champions": champs, "needs_check": check}
            stats["images"] += 1
            stats["labels"] += len(champs)
            stats["needs_check"] += len(check)
        (out / "ground_truth.json").write_text(json.dumps(gt, indent=1), encoding="utf-8")
        return stats
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle")
    ap.add_argument("--out")
    ap.add_argument("--min-id", type=float, default=0.85)
    ap.add_argument("--positions", help="JSON {positions: {game_time_s: {alias: [x, y]}}} (game units)")
    ap.add_argument("--check-dist", type=float, default=0.08)
    a = ap.parse_args()
    src = Path(a.bundle)
    out = Path(a.out) if a.out else CASES_DIR / src.stem
    pos = json.loads(Path(a.positions).read_text(encoding="utf-8")).get("positions") if a.positions else None
    stats = convert(src, out, a.min_id, pos, a.check_dist)
    print(f"{out}: {stats}  (review 'needs_check' in {out / 'ground_truth.json'})")


if __name__ == "__main__":
    main()
