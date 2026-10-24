"""FOG INTEL on REAL games: are the calls of treeaicoach/fog_intel.py right, and what do they change?

    python tools/fog_intel_truth.py <games dir>      (records *.json + truth/*.truth.json)

Oracle replay on the LCU truth files (per-minute true positions / gold / level of the 10 champions,
kills, epic monsters, see tools/team_calls_truth.py for the method and its limits: frames are 60 s
apart, so every count is a sample, not the whole game).

1. **Jungler at his base** (``gc_jg_based`` + the ``"base"`` source of macro.jungler_location): every
   laning minute (2:30-14:00) where the true enemy jungler stands alive in his fountain is a moment
   a purchase can tell. The window is fog_intel's floor (fountain -> my true position at the upper
   speed bound). Checked: my deaths with the jungler involved inside the window (must be 0), kills he
   took part in inside it (must be 0); and the record's jungler warnings inside it (fog danger
   "X peut arriver", "jungler invisible", first-gank forecast, gank alerts on him) with the truth
   verdict (ground_truth._classify): what the base fact removes or contradicts.
2. **Fight calculator** (``gc_obj_fight``): every minute >= 5:00 with the dragon / Baron up, the
   rule sees the true positions, death timers (from the kills) and the true total gold; the next
   90 s say who took the objective and who lost the fight near the pit.
3. **Full clear -> gank** (NOT built): level 4 time of the enemy jungler (xp interpolation), the
   kills he took part in within 75 s, and their map half vs his half at level 4.
"""

from __future__ import annotations

import glob
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from treeaicoach import fog_intel as FI  # noqa: E402
from treeaicoach import geometry  # noqa: E402
from treeaicoach import ground_truth as GT  # noqa: E402
from treeaicoach import macro as M  # noqa: E402
from team_calls_truth import PIT, obj_alive, respawn_s  # noqa: E402

JG_WARN = ("peut arriver", "invisible", "arrive de ton côté")


def _dead_at(d: dict, pos: dict, gt: float) -> dict[int, float]:
    dead = {}
    for k in d["kills"]:
        if k[0] <= gt:
            lvl = (pos.get(str(k[2])) or [0, 0, 0, 0, 0, 1])[5]
            end = k[0] + respawn_s(lvl, k[0])
            if end > gt:
                dead[k[2]] = end - gt
    return dead


def base_windows(d: dict, record: dict | None) -> dict:
    tr = GT._Truth(d)
    jg, me, team = tr.jungler, tr.me, tr.my_team
    other = M.OTHER.get(team)
    out = {"moments": 0, "window_s": [], "deaths_in": 0, "jg_kills_in": 0, "warn": {}}
    if jg is None or me is None or other is None:
        return out
    my_pos = GT._my_pos_fn(record or {}, tr)
    anchors = tr.anchors(jg)
    alerts = []
    for a in (record or {}).get("alerts") or []:
        if not isinstance(a, list) or len(a) < 4:
            continue
        kind, text, alias = str(a[1]), str(a[3] or ""), str(a[4] if len(a) > 4 and a[4] else "")
        is_jg = (kind == "jungler_approach" or (kind in GT.GANK_KINDS and tr.pid_of_alias(alias) == jg)
                 or any(w in text for w in JG_WARN))
        if is_jg:
            alerts.append((float(a[0]), kind if kind != "macro_tip" and kind != "personal_danger" else
                           ("fog" if "peut arriver" in text else "invisible" if "invisible" in text else "first_gank"
                            if "arrive de ton côté" in text else kind)))
    for gt, row in tr.frames:
        if not (150.0 <= gt < M.LANING_END_GT):
            continue
        j, m = row.get(str(jg)), row.get(str(me))
        if not j or not m or not geometry.in_fountain(j[0], j[1], other):
            continue
        if jg in _dead_at(d, row, gt) or me in _dead_at(d, row, gt):
            continue
        w = FI.fountain_eta(other, (m[0], m[1]))
        if w is None or w < FI.BASE_MIN_WINDOW_S:
            continue
        out["moments"] += 1
        out["window_s"].append(w)
        for t, killer, victim, assists, _uv in tr.kills:
            if gt < t <= gt + w:
                if killer == jg or jg in assists:
                    out["jg_kills_in"] += 1
                    if victim == me:
                        out["deaths_in"] += 1
        for t, kind in alerts:
            if gt + 2.0 <= t <= gt + w:
                verdict = GT._classify(t, my_pos(t), anchors, my_pos)
                out["warn"].setdefault(kind, []).append(verdict)
    return out


def fight_calls(d: dict) -> list[dict]:
    parts = {p["id"]: p for p in d["participants"]}
    me_id, my_team = d["me"], d["my_team"]
    jg = parts.get(d.get("enemy_jungler"), {}).get("alias", "")
    rows = []
    for gt, pos in d["frames"]:
        if gt < 300:
            continue
        dead = _dead_at(d, pos, gt)

        def player(pid: int) -> SimpleNamespace:
            p = parts[pid]
            return SimpleNamespace(champion_alias=p["alias"], champion_name=p["name"], is_dead=pid in dead,
                                   respawn_timer=dead.get(pid, 0.0), position=p["position"], level=pos[str(pid)][5],
                                   team=p["team"], items=[], gold=pos[str(pid)][2])
        enemies = [pid for pid, p in parts.items() if p["team"] != my_team]
        allies = [pid for pid, p in parts.items() if p["team"] == my_team and pid != me_id]
        seen = [SimpleNamespace(alias=parts[pid]["alias"], uv=tuple(pos[str(pid)][:2]), visible=True, hidden_s=0.0)
                for pid in enemies if pid not in dead]
        al_seen = [SimpleNamespace(alias=parts[pid]["alias"], uv=tuple(pos[str(pid)][:2]), visible=True, hidden_s=0.0)
                   for pid in allies if pid not in dead]
        game = SimpleNamespace(me=player(me_id), enemies=[player(p) for p in enemies],
                               allies=[player(p) for p in allies])
        objs = {k: (obj_alive(d["monsters"], k, gt), None) for k in PIT}
        me_uv = tuple(pos[str(me_id)][:2])
        ctx = M.MacroCtx(t=gt, gt=gt, phase="laning" if gt < 840 else "mid", my_team=my_team,
                         role=parts[me_id]["position"], me_uv=me_uv, me_alive=me_id not in dead,
                         in_base=geometry.in_fountain(me_uv[0], me_uv[1], my_team), enemies=seen, game=game,
                         objectives=objs, jungler_alias=jg.lower() or None, allies=al_seen)
        c = FI.rule_objective_fight(ctx, gold_of=lambda p: p.gold)
        if c is None:
            continue
        key = next((k for k in PIT if k in c.ident), None)
        if key is None:
            continue
        pit = PIT[key]
        types = ("BARON_NASHOR",) if key == "baron" else ("DRAGON", "ELDER_DRAGON")
        take = [m for m in d["monsters"] if gt <= m[0] <= gt + 90 and m[3] in types]
        near = [k for k in d["kills"] if gt <= k[0] <= gt + 90 and geometry.dist((k[4], k[5]), pit) < 0.3]
        rows.append({"game": d.get("record", "")[:22], "gt": gt, "key": key,
                     "verdict": "win" if ":win:" in c.ident else "lose", "text": c.text,
                     "took": None if not take else ("us" if take[0][2] == my_team else "them"),
                     "lost_us": sum(1 for k in near if parts[k[2]]["team"] == my_team),
                     "lost_them": sum(1 for k in near if parts[k[2]]["team"] != my_team)})
    return rows


def clear_gank(d: dict) -> tuple[int, int, int] | None:
    tr = GT._Truth(d)
    jg = tr.jungler
    ser = [(t, row.get(str(jg))) for t, row in tr.frames if row.get(str(jg))]
    for (t0, a), (t1, b) in zip(ser, ser[1:]):
        if a[3] < 1140 <= b[3]:
            k = (1140 - a[3]) / max(1, b[3] - a[3])
            tl = t0 + k * (t1 - t0)
            side = geometry.side_of(a[0] + k * (b[0] - a[0]), a[1] + k * (b[1] - a[1]))
            ks = [geometry.side_of(*uv) for t, ki, _v, asl, uv in tr.kills if tl <= t <= tl + 75 and (ki == jg or jg in asl)]
            return 1, int(bool(ks)), int(bool(ks) and ks[0] == side)
    return None


def main(argv: list[str]) -> int:
    root = argv[0] if argv else "."
    tot = {"moments": 0, "window_s": [], "deaths_in": 0, "jg_kills_in": 0, "warn": {}}
    fights: list[dict] = []
    cg = [0, 0, 0]
    n = 0
    for f in sorted(glob.glob(os.path.join(root, "truth", "*.truth.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
            rec_path = os.path.join(root, str(d.get("record") or ""))
            record = json.load(open(rec_path, encoding="utf-8")) if os.path.isfile(rec_path) else None
        except Exception as e:
            print(f"skip {os.path.basename(f)}: {e}")
            continue
        n += 1
        b = base_windows(d, record)
        for k in ("moments", "deaths_in", "jg_kills_in"):
            tot[k] += b[k]
        tot["window_s"] += b["window_s"]
        for k, v in b["warn"].items():
            tot["warn"].setdefault(k, []).extend(v)
        fights += fight_calls(d)
        c = clear_gank(d)
        if c:
            cg = [x + y for x, y in zip(cg, c)]
    ws = sorted(tot["window_s"])
    print(f"{n} parties vérifiées")
    print(f"1. jungler à la base (minutes 2:30-14:00 où il est dans sa fontaine) : {tot['moments']} moments, "
          f"fenêtre médiane {ws[len(ws) // 2]:.0f} s" if ws else "1. jungler à la base : aucun moment")
    print(f"   kills avec lui dans la fenêtre : {tot['jg_kills_in']} ; mes morts avec lui : {tot['deaths_in']}")
    for k, v in sorted(tot["warn"].items()):
        print(f"   alerte '{k}' dans la fenêtre : {len(v)} (confirmées {v.count('confirmed')}, fausses "
              f"{v.count('false') + v.count('probable_false')}, inconnues {v.count('unknown')})")
    for verdict in ("win", "lose"):
        rs = [r for r in fights if r["verdict"] == verdict]
        us = sum(r["took"] == "us" for r in rs)
        them = sum(r["took"] == "them" for r in rs)
        fw = sum(r["lost_them"] > r["lost_us"] for r in rs)
        fl = sum(r["lost_us"] > r["lost_them"] for r in rs)
        print(f"2. combat {verdict:4s} : {len(rs)} minutes, objectif pris par nous {us} / par eux {them}, "
              f"combats près du puits gagnés {fw} / perdus {fl}")
    for r in fights:
        print(f"   {r['game']} {int(r['gt']) // 60:02d}:00 {r['key']:6s} « {r['text']} » pris={r['took']} "
              f"morts nous={r['lost_us']} eux={r['lost_them']}")
    print(f"3. niveau 4 du jungler : {cg[0]} parties, kill avec lui en 75 s : {cg[1]}, même moitié : {cg[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
