"""How often the team calls (team_calls.rule_cross_objective) fire on REAL games, and would they
have been safe? Oracle replay on LCU truth files (per-minute true positions of the 10 champions,
kills, epic monsters): every minute the rule sees EVERY enemy at his true position (perfect
tracking = upper bound of what the app can call), then the next minute of the real game says
whether the call was safe.

    python tools/team_calls_truth.py <truth dir>

"Safe" = in the next 60 s no death of my team within 0.25 of the pit, the enemy does not take that
objective within 90 s, and at most one enemy ALIVE at the call stands within 0.30 of the pit at the
next frame (the dead ones respawning are the window the call already states). The
naive "3+ enemies on the other half" call (no guards) is measured the same way for comparison.
Frames are 60 s apart: positions between two frames are unknown (a coarse but honest check).
"""

from __future__ import annotations

import glob
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treeaicoach import geometry  # noqa: E402
from treeaicoach import macro as M  # noqa: E402
from treeaicoach.team_calls import rule_cross_objective  # noqa: E402

BRW = [10, 10, 12, 12, 14, 16, 20, 25, 28, 32.5, 35, 37.5, 40, 42.5, 45, 47.5, 50, 52.5]
PIT = {"baron": M.BARON_UV, "dragon": M.DRAGON_UV}


def respawn_s(level: int, gt: float) -> float:
    b = BRW[max(1, min(18, int(level or 1))) - 1]
    if gt > 900:
        b *= 1.0 + min(0.5, (gt / 60.0 - 15.0) * 0.0283)
    return b


def obj_alive(monsters: list, key: str, gt: float) -> bool:
    if key == "baron":
        kills = [m[0] for m in monsters if m[3] == "BARON_NASHOR"]
        first, again = 1200.0, 360.0
    else:
        kills = [m[0] for m in monsters if m[3] in ("DRAGON", "ELDER_DRAGON")]
        first, again = 300.0, 300.0
    if gt < first:
        return False
    last = max((k for k in kills if k <= gt), default=None)
    return last is None or gt - last >= again


def evaluate(path: str) -> list[dict]:
    d = json.load(open(path, encoding="utf-8"))
    parts = {p["id"]: p for p in d["participants"]}
    me_id, my_team = d["me"], d["my_team"]
    jg = parts.get(d.get("enemy_jungler"), {}).get("alias", "")
    frames = d["frames"]
    out = []
    for i, (gt, pos) in enumerate(frames[:-1]):
        if gt < 600:
            continue
        nxt_gt, nxt = frames[i + 1]
        dead = {}
        for k in d["kills"]:
            if k[0] <= gt:
                lvl = (pos.get(str(k[2])) or [0, 0, 0, 0, 0, 1])[5]
                end = k[0] + respawn_s(lvl, k[0])
                if end > gt:
                    dead[k[2]] = end - gt

        def player(pid: int) -> SimpleNamespace:
            p = parts[pid]
            return SimpleNamespace(champion_alias=p["alias"], champion_name=p["name"], is_dead=pid in dead,
                                   respawn_timer=dead.get(pid, 0.0), position=p["position"], level=pos[str(pid)][5],
                                   team=p["team"])
        enemies = [pid for pid, p in parts.items() if p["team"] != my_team]
        allies = [pid for pid, p in parts.items() if p["team"] == my_team and pid != me_id]
        seen = [SimpleNamespace(alias=parts[pid]["alias"], uv=tuple(pos[str(pid)][:2]), visible=True, hidden_s=0.0)
                for pid in enemies if pid not in dead]
        al_seen = [SimpleNamespace(alias=parts[pid]["alias"], uv=tuple(pos[str(pid)][:2]), visible=True, hidden_s=0.0)
                   for pid in allies if pid not in dead]
        game = SimpleNamespace(me=player(me_id), enemies=[player(p) for p in enemies], allies=[player(p) for p in allies])
        objs = {k: (obj_alive(d["monsters"], k, gt), None) for k in PIT}
        me_uv = tuple(pos[str(me_id)][:2])
        ctx = M.MacroCtx(t=gt, gt=gt, phase="laning" if gt < 840 else "mid", my_team=my_team,
                         role=parts[me_id]["position"], me_uv=me_uv, me_alive=me_id not in dead,
                         in_base=geometry.in_fountain(me_uv[0], me_uv[1], my_team), enemies=seen, game=game,
                         objectives=objs, jungler_alias=jg.lower() or None, allies=al_seen)
        call = rule_cross_objective(ctx)
        for rname, rule in (("fight_won", M._rule_fight_won), ("jungler_dead", M._rule_jungler_dead)):
            c2 = rule(ctx)
            tgt = getattr(c2, "target", None)
            key2 = next((k for k, v in PIT.items() if tgt is not None and geometry.dist(tgt, v) < 0.05), None)
            if c2 is None or key2 is None:
                continue
            pit = PIT[key2]
            types = ("BARON_NASHOR",) if key2 == "baron" else ("DRAGON", "ELDER_DRAGON")
            deaths = [k for k in d["kills"] if gt <= k[0] <= gt + 60 and parts[k[2]]["team"] == my_team
                      and geometry.dist((k[4], k[5]), pit) < 0.25]
            e_take = any(gt <= m[0] <= gt + 90 and m[3] in types and m[2] != my_team for m in d["monsters"])
            near_next = sum(1 for pid in enemies if pid not in dead and geometry.dist(tuple(nxt[str(pid)][:2]), pit) < 0.30)
            out.append({"game": os.path.basename(path)[:22], "gt": gt, "key": f"{rname}:{key2}", "naive": False,
                        "fired": True, "safe": not deaths and not e_take and near_next <= 1,
                        "we_took": any(gt <= m[0] <= gt + 90 and m[3] in types and m[2] == my_team for m in d["monsters"]),
                        "deaths": len(deaths), "enemy_take": e_take, "near_next": near_next, "text": c2.text})
        for key in PIT:
            if not objs[key][0]:
                continue
            pit = PIT[key]
            other = "bot" if key == "baron" else "top"
            n_other = sum(1 for s in seen if M.side_of(s.uv) == other)
            naive = n_other >= 3 and ctx.me_alive and (key != "baron" or gt >= 1200)
            fired = call is not None and call.title.upper().startswith(M.OBJ_TITLE[key][:4])
            if not (naive or fired):
                continue
            my_deaths = [k for k in d["kills"] if gt <= k[0] <= gt + 60 and parts[k[2]]["team"] == my_team
                         and geometry.dist((k[4], k[5]), pit) < 0.25]
            types = ("BARON_NASHOR",) if key == "baron" else ("DRAGON", "ELDER_DRAGON")
            enemy_take = [m for m in d["monsters"] if gt <= m[0] <= gt + 90 and m[3] in types and m[2] != my_team]
            we_take = [m for m in d["monsters"] if gt <= m[0] <= gt + 90 and m[3] in types and m[2] == my_team]
            near_next = sum(1 for pid in enemies if pid not in dead and geometry.dist(tuple(nxt[str(pid)][:2]), pit) < 0.30)
            safe = not my_deaths and not enemy_take and near_next <= 1
            out.append({"game": os.path.basename(path)[:22], "gt": gt, "key": key, "naive": naive, "fired": fired,
                        "safe": safe, "we_took": bool(we_take), "deaths": len(my_deaths), "enemy_take": bool(enemy_take),
                        "near_next": near_next, "text": call.text if fired else ""})
    return out


def main(argv: list[str]) -> int:
    rows = []
    for f in sorted(glob.glob(os.path.join(argv[0] if argv else ".", "*.truth.json"))):
        try:
            rows += evaluate(f)
        except Exception as e:  # a short / broken file
            print(f"skip {os.path.basename(f)}: {e}")
    keys = list(PIT) + sorted({r["key"] for r in rows if ":" in r["key"]})
    for key in keys:
        for what in ("naive", "fired"):
            rs = [r for r in rows if r["key"] == key and r[what]]
            ok = sum(r["safe"] for r in rs)
            took = sum(r["we_took"] for r in rs)
            pct = f"{100 * ok / len(rs):.0f} %" if rs else "-"
            print(f"{key:7s} {'règle' if what == 'fired' else 'naïf ':6s} : {len(rs):3d} minutes, sûr {ok:3d} ({pct}), "
                  f"pris par nous en 90 s {took}")
    for r in rows:
        if r["fired"]:
            print(f"  {r['game']} {int(r['gt']) // 60:02d}:00 {r['key']:6s} {'SÛR ' if r['safe'] else 'RISQUÉ'} "
                  f"morts={r['deaths']} pris_par_eux={r['enemy_take']} ennemis_près={r['near_next']} « {r['text']} »")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
