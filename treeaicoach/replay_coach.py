"""POST-GAME REPLAY COACH: the 3 decisive moments of a game, each with what happened and what to do instead.

From the League Client timeline of the game (``ground_truth`` file: per-minute positions, gold and
levels of the 10 players + kills, epic monsters, buildings) or, before it arrives / without the
client, the app's own record (Live Client event feed), three kinds of candidate moments are scored:

* ``swing`` - a cluster of events (kills, objectives, structures less than :data:`CLUSTER_GAP_S`
  apart) and the win-probability swing it caused, with the logistic model of :mod:`treeaicoach.hype`
  (gold lead, kills, towers, dragons, Baron / Elder buffs, inhibitors, levels). The gold lead before
  the cluster comes from the client frames (exact team gold, interpolated) or, without them, from the
  events already scored; the cluster adds the gold value of its own events (postgame_edge EV table).
* ``death_obj`` - one of us dies and the enemy takes an epic monster / a structure within
  :data:`DEATH_OBJ_S`: the death opened it.
* ``missed`` - we win a fight (2+ kills more) while a dragon / the Baron is up (or spawns within 30 s)
  and take nothing in the next :data:`MISS_S`: a free objective left on the map.

The 3 best moments (actionable ones first: a positive swing counts half) at least
:data:`MIN_GAP_S` apart are returned in game order, each as ONE French line with its timestamp:
"14:20 · Tu meurs seul (rivière du bas) et l'ennemi prend le dragon 35 s après. À faire : à une
minute d'un objectif, reste avec ton équipe.". Shown in the report (report.py) and, first one only,
in the Discord team recap (postgame_edge.team_recap, ``discord_team_recap``).

On the user's 17 real games of 10 min or more (client timeline + record): 49 moments (23 deaths that
gave an objective, 22 swings, 4 missed windows), 27 involving the player himself; 40 / 49 are found
the same (within 45 s) from the record alone, i.e. in the Discord recap posted before the client
timeline arrives.

Pure, never raises (``[]`` on unusable input). Names are champion names only.
"""

from __future__ import annotations

import bisect
import logging
from typing import Any

from treeaicoach.fmtutil import clock, finite_loose as _f

log = logging.getLogger(__name__)

CLUSTER_GAP_S = 30.0
CLUSTER_MAX_S = 60.0           # a cluster never spans more than this (a long skirmish = several moments)
DEATH_OBJ_S = 90.0
MISS_S = 90.0
MIN_GAP_S = 45.0
MIN_SWING = 0.04
MIN_SWING_POSITIVE = 0.08      # a good moment must be big to be one of the 3
POSITIVE_WEIGHT = 0.5
ALONE_R = 0.15                 # no ally this close at the death frame: "seul"
LIMIT = 3
EV_KILL, EV_KILL_PER_MIN = 300.0, 10.0
EV_VALUE = {"tower": 700.0, "inhib": 1500.0, "dragon": 600.0, "elder": 2500.0, "baron": 2500.0, "herald": 500.0,
            "grubs": 150.0}
EPIC = ("dragon", "elder", "baron", "herald", "grubs")
OBJ_FR = {"tower": "une tour", "inhib": "un inhibiteur", "dragon": "le dragon", "elder": "le dragon ancestral",
          "baron": "le Baron", "herald": "le Héraut", "grubs": "les larves"}
OBJ_DE = {"tower": "d'une tour", "inhib": "d'un inhibiteur", "dragon": "du dragon", "elder": "de l'ancestral",
          "baron": "du Baron", "herald": "du Héraut", "grubs": "des larves"}
_MONSTER = {"DRAGON": "dragon", "BARON_NASHOR": "baron", "RIFTHERALD": "herald", "HORDE": "grubs"}
_EVENT = {"DragonKill": "dragon", "BaronKill": "baron", "HeraldKill": "herald", "HordeKill": "grubs", "TurretKilled": "tower", "InhibKilled": "inhib"}
#: first spawn / respawn after a kill (s) of the objectives a "missed" window looks at
SPAWN = {"dragon": (300.0, 300.0), "baron": (1200.0, 360.0)}


def _other(team: str) -> str:
    return "CHAOS" if team == "ORDER" else "ORDER"


def _k(x: Any) -> str:
    return "".join(ch for ch in str(x or "").casefold() if ch.isalnum())


# ----------------------------------------------------------------------------- events
def _truth_events(truth: dict) -> tuple[list[dict], str, Any]:
    parts = {int(p["id"]): p for p in truth.get("participants") or [] if isinstance(p, dict) and "id" in p}
    team = str(truth.get("my_team") or "").upper()
    me = truth.get("me")
    out = []
    for k in truth.get("kills") or []:
        try:
            t, killer, victim = float(k[0]), int(k[1]), int(k[2])
        except (TypeError, ValueError, IndexError):
            continue
        vp = parts.get(victim)
        if vp is None:
            continue
        uv = (_f(k[4]), _f(k[5])) if len(k) >= 6 else (None, None)
        out.append({"t": t, "kind": "kill", "team": _other(vp["team"]), "victim_me": victim == me,
                    "victim": str(vp.get("name") or vp.get("alias") or ""), "victim_id": victim,
                    "killer": str((parts.get(killer) or {}).get("name") or ""), "uv": uv if None not in uv else None})
    for m in truth.get("monsters") or []:
        try:
            t, mteam, typ = float(m[0]), str(m[2]).upper(), str(m[3]).upper()
        except (TypeError, ValueError, IndexError):
            continue
        kind = _MONSTER.get(typ)
        if kind == "dragon" and "ELDER" in str(m[4] if len(m) > 4 else "").upper():
            kind = "elder"
        if kind and mteam in ("ORDER", "CHAOS"):
            out.append({"t": t, "kind": kind, "team": mteam})
    for b in truth.get("buildings") or []:
        try:
            t, owner, typ = float(b[0]), str(b[2]).upper(), str(b[3]).upper()
        except (TypeError, ValueError, IndexError):
            continue
        kind = "inhib" if "INHIBITOR" in typ else "tower" if "TOWER" in typ else None
        if kind and owner in ("ORDER", "CHAOS"):
            out.append({"t": t, "kind": kind, "team": _other(owner)})
    out.sort(key=lambda e: e["t"])
    return out, team, me


def _record_events(record: dict) -> tuple[list[dict], str]:
    from treeaicoach.postgame_edge import _my_keys, _my_team, _name_teams, _structure_owner

    teams = _name_teams(record)
    mine = _my_keys(record)
    champ = {}
    for p in record.get("roster") or []:
        if isinstance(p, dict):
            for key in ("alias", "name", "riot_id", "summoner_name", "riot_id_game_name"):
                if p.get(key):
                    champ[_k(p[key])] = str(p.get("name") or p.get("alias") or "")
                    champ[_k(str(p[key]).split("#")[0])] = str(p.get("name") or p.get("alias") or "")
    team = _my_team(record)
    out = []
    for e in record.get("events") or []:
        if not isinstance(e, dict):
            continue
        name, t = str(e.get("EventName") or ""), _f(e.get("EventTime"))
        if t is None:
            continue
        kt = teams.get(_k(e.get("KillerName"))) or _structure_owner(e.get("KillerName"))
        if kt is None:
            kt = next((teams[a] for a in (_k(x) for x in e.get("Assisters") or []) if a in teams), None)
        if name == "ChampionKill":
            vt = teams.get(_k(e.get("VictimName")))
            if vt is None:
                continue
            out.append({"t": t, "kind": "kill", "team": _other(vt), "victim_me": _k(e.get("VictimName")) in mine,
                        "victim": champ.get(_k(e.get("VictimName")), ""), "victim_id": _k(e.get("VictimName")),
                        "killer": champ.get(_k(e.get("KillerName")), ""), "uv": None})
        elif name in ("TurretKilled", "InhibKilled"):
            owner = _structure_owner(e.get(name))
            gain = _other(owner) if owner else kt
            if gain:
                out.append({"t": t, "kind": _EVENT[name], "team": gain})
        elif name in _EVENT and kt:
            kind = _EVENT[name]
            if kind == "dragon" and str(e.get("DragonType") or "").lower() == "elder":
                kind = "elder"
            out.append({"t": t, "kind": kind, "team": kt})
    out.sort(key=lambda x: x["t"])
    return out, team


# ----------------------------------------------------------------------------- win model
class _Frames:
    """Team gold / level difference (my team - theirs) from the client frames, interpolated."""

    def __init__(self, truth: dict | None, team: str) -> None:
        self.ts: list[float] = []
        self.gold: list[float] = []
        self.lvl: list[float] = []
        self.pos: list[dict] = []
        if not truth:
            return
        parts = {str(p["id"]): p for p in truth.get("participants") or [] if isinstance(p, dict) and "id" in p}
        for fr in truth.get("frames") or []:
            try:
                gt, row = float(fr[0]), fr[1]
                g = sum((1 if parts[k]["team"] == team else -1) * float(v[2]) for k, v in row.items() if k in parts)
                lv = sum((1 if parts[k]["team"] == team else -1) * float(v[5]) for k, v in row.items() if k in parts) / 5.0
            except (TypeError, ValueError, KeyError, IndexError):
                continue
            self.ts.append(gt)
            self.gold.append(g)
            self.lvl.append(lv)
            self.pos.append(row)

    def at(self, t: float) -> tuple[float, float] | None:
        if not self.ts:
            return None
        i = bisect.bisect_right(self.ts, t)
        if i == 0:
            return self.gold[0], self.lvl[0]
        if i >= len(self.ts):
            return self.gold[-1], self.lvl[-1]
        w = (t - self.ts[i - 1]) / max(1e-6, self.ts[i] - self.ts[i - 1])
        return (self.gold[i - 1] + (self.gold[i] - self.gold[i - 1]) * w,
                self.lvl[i - 1] + (self.lvl[i] - self.lvl[i - 1]) * w)

    def row_at(self, t: float) -> dict | None:
        if not self.ts:
            return None
        i = min(range(len(self.ts)), key=lambda j: abs(self.ts[j] - t))
        return self.pos[i] if abs(self.ts[i] - t) <= 45.0 else None


def _value(e: dict) -> float:
    if e["kind"] == "kill":
        return EV_KILL + EV_KILL_PER_MIN * e["t"] / 60.0
    return EV_VALUE.get(e["kind"], 0.0)


def _win_p(state: dict, gold: float, lvl: float, t: float) -> float:
    from treeaicoach.hype import WinFactors, win_probability

    return win_probability(WinFactors(
        game_time=t, gold_diff=gold, kill_diff=state["kills"], tower_diff=state["towers"],
        dragon_diff=state["dragons"], baron_diff=state["barons"],
        baron_buff=sum(s for tt, s in state["baron_t"] if 0 <= t - tt < 180.0),
        elder_buff=sum(s for tt, s in state["elder_t"] if 0 <= t - tt < 150.0),
        inhib_diff=sum(s for tt, s in state["inhib_t"] if 0 <= t - tt < 300.0), level_diff=lvl))


def _apply(state: dict, e: dict, team: str) -> None:
    s = 1 if e["team"] == team else -1
    k = e["kind"]
    if k == "kill":
        state["kills"] += s
    elif k == "tower":
        state["towers"] += s
    elif k == "dragon":
        state["dragons"] += s
    elif k == "baron":
        state["barons"] += s
        state["baron_t"].append((e["t"], s))
    elif k == "elder":
        state["elder_t"].append((e["t"], s))
    elif k == "inhib":
        state["inhib_t"].append((e["t"], s))
    state["lead"] += s * _value(e)


# ----------------------------------------------------------------------------- moments
def _alive_counts(evs: list[dict], t: float, team: str) -> tuple[int, int]:
    """Players alive (me + allies, enemies) at ``t`` from the kills before it (death ~ 10 + 2.5 s / min)."""
    us = them = 5
    for e in evs:
        if e["kind"] != "kill" or e["t"] > t:
            continue
        if t - e["t"] < 10.0 + 2.5 * e["t"] / 60.0:
            if e["team"] == team:
                them -= 1
            else:
                us -= 1
    return max(0, us), max(0, them)


def _obj_up(evs: list[dict], key: str, t: float) -> bool:
    first, again = SPAWN[key]
    kills = [e["t"] for e in evs if e["kind"] in ((key, "elder") if key == "dragon" else (key,)) and e["t"] <= t]
    spawn = (kills[-1] + again) if kills else first
    return t + 30.0 >= spawn


def _where(e: dict, frames: _Frames, team: str, truth: dict | None) -> tuple[str, bool | None]:
    """(French place of a death, alone?) from the kill position and the nearest frame."""
    uv = e.get("uv")
    if not uv:
        return "", None
    try:
        from treeaicoach import geometry
        from treeaicoach.postgame_edge import _death_zone

        place = _death_zone(uv[0], uv[1], team)[1]
        row = frames.row_at(e["t"])
        alone = None
        if row is not None and truth:
            parts = {str(p["id"]): p for p in truth.get("participants") or [] if isinstance(p, dict)}
            vid = str(e.get("victim_id"))
            near = [k for k, v in row.items() if k in parts and k != vid and parts[k]["team"] == parts.get(vid, {}).get("team")
                    and geometry.dist((v[0], v[1]), uv) < ALONE_R]
            alone = not near
        return place, alone
    except Exception:
        return "", None


def _fight_desc(c: list[dict], team: str) -> tuple[int, int, bool]:
    a = sum(1 for e in c if e["kind"] == "kill" and e["team"] == team)
    b = sum(1 for e in c if e["kind"] == "kill" and e["team"] != team)
    return a, b, any(e.get("victim_me") for e in c if e["kind"] == "kill")


def decisive_moments(record: Any, truth: Any = None, limit: int = LIMIT) -> list[dict[str, Any]]:
    """The ``limit`` decisive moments, chronological: ``{"t", "time", "kind", "what", "instead",
    "line", "swing", "before", "after"}``. ``[]`` without usable data. Never raises."""
    try:
        rec = record if isinstance(record, dict) else {}
        tr = truth if isinstance(truth, dict) and truth.get("frames") else None
        if tr is not None:
            evs, team, _me = _truth_events(tr)
        else:
            evs, team = _record_events(rec)
        if team not in ("ORDER", "CHAOS") or not evs:
            return []
        frames = _Frames(tr, team)
        clusters: list[list[dict]] = []
        for e in evs:
            if clusters and e["t"] - clusters[-1][-1]["t"] <= CLUSTER_GAP_S \
                    and e["t"] - clusters[-1][0]["t"] <= CLUSTER_MAX_S:
                clusters[-1].append(e)
            else:
                clusters.append([e])
        state = {"kills": 0, "towers": 0, "dragons": 0, "barons": 0, "baron_t": [], "elder_t": [], "inhib_t": [],
                 "lead": 0.0}
        infos: list[dict] = []                    # pass 1: each cluster's win-probability swing
        for c in clusters:
            t0, t1 = c[0]["t"], c[-1]["t"]
            fr = frames.at(t0 - 1.0)
            gold0, lvl = (fr if fr is not None else (state["lead"], 0.0))
            p0 = _win_p(state, gold0, lvl, t0)
            lead_before = state["lead"]
            for e in c:
                _apply(state, e, team)
            gold1 = gold0 + state["lead"] - lead_before
            p1 = _win_p(state, gold1, lvl, t1)
            a, b, me_dead = _fight_desc(c, team)
            objs = [e for e in c if e["kind"] != "kill"]
            best = max(objs, key=_value) if objs else None
            up = [k for k in ("baron", "dragon") if _obj_up(evs, k, t1) and (k != "baron" or t1 >= 1200.0)]
            gain = (_win_p(state, gold1 + EV_VALUE[up[0]], lvl, t1) - p1) if up else 0.0
            infos.append({"c": c, "t0": t0, "t1": t1, "p0": p0, "p1": p1, "d": p1 - p0, "a": a, "b": b,
                          "me_dead": me_dead, "best": best, "up": up, "gain": gain})
        cands: list[dict] = []
        used: set[int] = set()
        for i, inf in enumerate(infos):            # pass 2: the candidate moments
            if i in used:
                continue
            c, t0, t1, p0, p1, d = inf["c"], inf["t0"], inf["t1"], inf["p0"], inf["p1"], inf["d"]
            a, b, me_dead, best = inf["a"], inf["b"], inf["me_dead"], inf["best"]
            what = []
            if a or b:
                if a > b:
                    what.append(f"combat gagné {a} à {b}")
                elif b > a:
                    what.append(f"combat perdu {a} à {b}" + (" (tu es mort dedans)" if me_dead else ""))
                else:
                    what.append(f"échange {a} partout")
            if best is not None:
                what.append(f"{OBJ_FR[best['kind']]} pour {'vous' if best['team'] == team else 'l’ennemi'}")
            desc = " puis ".join(what) or "série d'événements"
            # 1. deaths that gave an objective (inside this cluster or right after it)
            deaths = [e for e in c if e["kind"] == "kill" and e["team"] != team]
            taken = [e for e in evs if e["kind"] != "kill" and e["team"] != team and deaths
                     and 0.0 <= e["t"] - deaths[0]["t"] <= DEATH_OBJ_S
                     and (e["kind"] in EPIC or e["kind"] == "inhib")]
            if deaths and taken and b > a:
                obj = max(taken, key=_value)
                j = next((k for k in range(i, len(infos)) if any(e is obj for e in infos[k]["c"])), i)
                used.update(range(i, j + 1))       # the objective's own cluster is this moment's end
                p_end = infos[j]["p1"]
                v = next((e for e in deaths if e.get("victim_me") and e["t"] <= obj["t"]), deaths[0])
                place, alone = _where(v, frames, team, tr)
                who = "Tu meurs" if v.get("victim_me") else f"{v.get('victim') or 'Un allié'} meurt"
                if alone:
                    who += " seul"
                if place:
                    who += f" ({place})"
                dt = int(round(obj["t"] - v["t"]))
                lost = sum(1 for e in deaths if e["t"] <= obj["t"])
                what_txt = (f"{who}" + (f", {lost} morts chez vous," if lost > 1 else "")
                            + f" et l’ennemi prend {OBJ_FR[obj['kind']]} {dt} s après")
                if v.get("victim_me") and alone:
                    instead = f"à l'approche {OBJ_DE[obj['kind']]}, ne reste pas seul : recule vers ton équipe"
                elif alone:
                    instead = f"à l'approche {OBJ_DE[obj['kind']]}, groupez-vous avant de poser la vision"
                else:
                    instead = "à moins nombreux, cédez l'objectif et prenez ailleurs au lieu de vous battre"
                cands.append({"t": v["t"], "kind": "death_obj", "what": what_txt, "instead": instead,
                              "score": abs(p_end - p0) + 0.02, "swing": p_end - p0, "before": p0, "after": p_end})
                continue
            # 2. a won fight with an objective up and nothing taken
            if a - b >= 2 and inf["up"] and not any(e["kind"] != "kill" and e["team"] == team for e in c):
                after = [e for e in evs if t1 < e["t"] <= t1 + MISS_S and e["kind"] != "kill" and e["team"] == team]
                if not after:
                    key = inf["up"][0]
                    _us, them = _alive_counts(evs, t1, team)
                    cands.append({"t": t0, "kind": "missed",
                                  "what": f"combat gagné {a} à {b} mais rien pris derrière ({OBJ_FR[key]} était là)",
                                  "instead": f"enchaînez tout de suite sur {OBJ_FR[key]}, ils n'étaient que {them} en vie",
                                  "score": max(inf["gain"], 0.0) + 0.03, "swing": d, "before": p0, "after": p1})
                    continue
            # 3. the swing itself
            if abs(d) < (MIN_SWING if d < 0 else MIN_SWING_POSITIVE):
                continue
            if d < 0:
                if me_dead and (a or b):
                    instead = "à moins nombreux, recule et attends ton équipe"
                elif b > a:
                    instead = "comptez-vous avant d'engager, à moins nombreux reculez"
                elif best is not None and best["kind"] in EPIC:
                    instead = (f"soyez là une minute avant l'apparition {OBJ_DE[best['kind']]} "
                               "avec la vision posée")
                else:
                    instead = "défendez la tour à plusieurs ou prenez un objectif de l'autre côté"
                score = abs(d)
            else:
                instead = "à refaire, c'est ce jeu groupé qui gagne"
                score = abs(d) * POSITIVE_WEIGHT
            cands.append({"t": t0, "kind": "swing", "what": desc[0].upper() + desc[1:], "instead": instead,
                          "score": score, "swing": d, "before": p0, "after": p1})
        picked: list[dict] = []
        for c in sorted(cands, key=lambda x: -x["score"]):
            if all(abs(c["t"] - p["t"]) >= MIN_GAP_S for p in picked):
                picked.append(c)
            if len(picked) >= max(0, int(limit)):
                break
        out = []
        for c in sorted(picked, key=lambda x: x["t"]):
            tm = clock(c["t"], "?", rounded=True)
            what = c["what"][0].upper() + c["what"][1:]
            instead = c["instead"][0].upper() + c["instead"][1:]
            out.append({"t": round(c["t"], 1), "time": tm, "kind": c["kind"], "what": what.replace("’", "'"),
                        "instead": instead, "swing": round(c["swing"], 3), "before": round(c["before"], 3),
                        "after": round(c["after"], 3),
                        "line": f"{tm} · {what}. À faire : {instead}.".replace("’", "'")})
        return out
    except Exception:
        log.exception("decisive_moments failed")
        return []


def recap_line(moments: list[dict], me_name: str = "") -> str:
    """One short line for the Discord recap (the most decisive moment), ``""`` when none. The voice
    channel reads it: "tu" (the player) becomes his champion ``me_name``."""
    try:
        if not moments:
            return ""
        m = max(moments, key=lambda x: abs(_f(x.get("swing")) or 0.0))
        what, instead = str(m["what"]), str(m["instead"])
        if me_name:
            what = what.replace("Tu meurs", f"{me_name} meurt").replace("(tu es mort dedans)", f"({me_name} mort dedans)")
            instead = instead.replace("ne reste pas seul : recule vers ton équipe", "ne restez pas seuls")
            instead = instead.replace("recule et attends ton équipe", "reculez et attendez l'équipe")
        return f"Moment décisif {m['time']} : {what}. À faire : {instead}."
    except Exception:
        return ""


__all__ = ["decisive_moments", "recap_line"]
