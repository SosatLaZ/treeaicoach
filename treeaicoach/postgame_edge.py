"""Post-game edge: what the big analysis sites show, computed from the player's OWN games only.

Pure module (no Qt, no network, never raises). Inputs are the recorder's game record (``recorder.py``),
the optional League Client truth of the same game (``ground_truth.build_truth``) and the per-game rows of
:func:`treeaicoach.progress.collect` (which caches :func:`game_features` next to the records).

One game (report of the game just played):

* :func:`lane_diffs` - gold / XP / CS diff at 10 and 15 min against the lane opponent (client timeline
  when present, else the recorded Tab scoreboard, estimated) + one verdict line.
* :func:`item_timing` - minute of the 1st / 2nd legendary item against a bracket target, and the gold
  left unspent while alive ("or qui dort").
* :func:`turning_point` - the event cluster (a fight, an objective) where the estimated win probability
  swung most, in one sentence with its timestamp.
* :func:`team_recap` - the Discord text-channel recap: per friend of the voice channel, one key stat and
  one fix. Discord names are passed in memory by the caller, nothing is written to disk.

Many games (Analyse page + report):

* :func:`death_zones` - every recorded death on one map (heat map points) and the top recurring death
  zone in words ("40 % de tes morts : rivière du bas, jungler ennemi invisible").
* :func:`profile` - six-axis personal score (farm, vision, combat, survie, objectifs, agressivité),
  0-100, with the trend over the last 10 games.
* :func:`champion_pool` - per-champion and per-matchup win rate / KDA from the player's own history.

Privacy: only champion aliases, positions and numbers are returned or cached; never a Riot ID or a
summoner name (the record's own names are only used in memory to find who killed whom).
"""

from __future__ import annotations

import logging
import math
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

from treeaicoach.fmtutil import clock, finite_loose as _f

log = logging.getLogger(__name__)

MIN_GAME_S = 300.0
LANE_MINUTES = (10, 15)
FRAME_TOL_S = 45.0

#: minute of the 1st / 2nd completed legendary item, per bracket and role (solo queue averages,
#: rounded: beginners finish the first item late, mostly because of missed CS and late recalls)
ITEM_TARGETS: dict[str, dict[str, tuple[float, float]]] = {
    "debutant": {"TOP": (14.0, 23.0), "JUNGLE": (13.5, 22.0), "MIDDLE": (13.5, 22.0), "BOTTOM": (14.0, 22.5),
                 "UTILITY": (16.0, 26.0)},
    "argent-or": {"TOP": (12.5, 20.5), "JUNGLE": (12.0, 20.0), "MIDDLE": (12.0, 20.0), "BOTTOM": (12.5, 20.0),
                  "UTILITY": (14.5, 24.0)},
    "platine+": {"TOP": (11.0, 18.5), "JUNGLE": (10.5, 18.0), "MIDDLE": (10.5, 18.0), "BOTTOM": (11.0, 18.0),
                 "UTILITY": (13.0, 22.0)},
}
BRACKET_LABEL = {"debutant": "débutant", "argent-or": "argent / or", "platine+": "platine et plus"}
DEFAULT_BRACKET = "debutant"
LEGENDARY_GOLD = 2200            # fallback when the item table is unavailable
IDLE_GOLD = 1300                 # enough for a component: carrying more while alive = gold that sleeps
UNSPENT_SKIP_S = 90.0            # the starting gold is not "unspent"
LATE_START_S = 120.0             # first snapshot later than this: the app started mid-game

CS_TARGET = {"TOP": 7.0, "MIDDLE": 7.0, "BOTTOM": 7.5, "JUNGLE": 5.5}
VISION_TARGET = {"UTILITY": 2.0, "JUNGLE": 1.0}
VISION_TARGET_DEFAULT = 0.6
SUPPORT_GOLD_TARGET = 260.0      # gold per minute of a support (farm axis)

AXES: tuple[tuple[str, str], ...] = (("farm", "Farm"), ("vision", "Vision"), ("fight", "Combat"),
                                     ("survival", "Survie"), ("objectives", "Objectifs"),
                                     ("aggression", "Agressivité"))

# win-probability model of :func:`turning_point` (gold-equivalent swing of each event)
EV_KILL = 300.0
EV_KILL_PER_MIN = 10.0           # a late death costs more (death timer)
EV_VALUE = {"TurretKilled": 700.0, "InhibKilled": 1500.0, "DragonKill": 600.0, "BaronKill": 2500.0,
            "HeraldKill": 500.0, "HordeKill": 150.0}
EV_ELDER = 2500.0
CLUSTER_GAP_S = 30.0           # a fight and the objective taken right after it = one moment


def _str(x: Any) -> str:
    return x.strip() if isinstance(x, str) else ("" if x is None else str(x).strip())


def _key(x: Any) -> str:
    s = unicodedata.normalize("NFKD", _str(x).split("#", 1)[0])
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return "".join(ch for ch in s.casefold() if ch.isalnum())


def _num(x: Any, decimals: int = 0, signed: bool = False) -> str:
    v = _f(x)
    if v is None:
        return "-"
    s = f"{v:+.{decimals}f}" if signed else f"{v:.{decimals}f}"
    return s.replace(".", ",").replace("-", "−")


def _pct(x: Any) -> str:
    v = _f(x)
    return "-" if v is None else f"{round(100 * v)} %"


def _list(x: Any) -> list:
    return x if isinstance(x, list) else []


def _dict(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _meta(record: Any) -> dict:
    return _dict(_dict(record).get("meta"))


def _position(record: Any) -> str:
    m = _meta(record)
    return _str(m.get("position") or _dict(_dict(record).get("summary")).get("position")).upper()


def _my_team(record: Any) -> str:
    return _str(_meta(record).get("team") or _dict(_dict(record).get("summary")).get("team")).upper()


def _duration(record: Any) -> float:
    r = _dict(record)
    d = _f(r.get("duration")) or _f(_dict(r.get("summary")).get("duration"))
    if d is None:
        snaps = _list(r.get("snapshots"))
        d = _f(_dict(snaps[-1]).get("game_time")) if snaps else None
    return float(d or 0.0)


def _enemy_half(u: float, v: float, team: str) -> bool:
    """Past the river diagonal (ORDER base bottom-left, CHAOS top-right), with a small dead band."""
    d = u - v
    return d > 0.05 if team != "CHAOS" else d < -0.05


# ======================================================================================
# 14. lane diffs at 10 / 15 min
# ======================================================================================
def _truth_row(truth: dict, minute: int, pid: Any) -> list | None:
    if pid is None:
        return None
    best, best_dt = None, FRAME_TOL_S + 1
    for fr in _list(truth.get("frames")):
        if not isinstance(fr, list) or len(fr) < 2 or not isinstance(fr[1], dict):
            continue
        t = _f(fr[0])
        if t is None:
            continue
        dt = abs(t - minute * 60.0)
        row = fr[1].get(str(pid))
        if dt < best_dt and isinstance(row, list) and len(row) >= 6:
            best, best_dt = row, dt
    return best


def _diff(a: Any, b: Any) -> float | None:
    x, y = _f(a), _f(b)
    return None if x is None or y is None else x - y


def lane_diffs(record: Any, truth: Any = None) -> dict[str, Any]:
    """``{"source": "client" | "tab" | None, "opponent", "rows": [{minute, gold_diff, xp_diff,
    cs_diff, level_diff}], "verdict"}``. The client timeline is exact; the Tab scoreboard values
    are estimated from item gold (no XP)."""
    out: dict[str, Any] = {"source": None, "opponent": None, "rows": [], "verdict": ""}
    try:
        role = _position(record)
        t = truth if isinstance(truth, dict) else {}
        if t.get("frames") and t.get("me") is not None and t.get("lane_opponent") is not None:
            me, opp = t.get("me"), t.get("lane_opponent")
            parts = {p.get("id"): p for p in _list(t.get("participants")) if isinstance(p, dict)}
            out["opponent"] = _str(_dict(parts.get(opp)).get("name") or _dict(parts.get(opp)).get("alias")) or None
            dur = _duration(record) or _f(t.get("duration")) or 0.0
            for m in LANE_MINUTES:
                if dur and m * 60.0 > dur + 5.0:
                    continue
                a, b = _truth_row(t, m, me), _truth_row(t, m, opp)
                if a is None or b is None:
                    continue
                out["rows"].append({"minute": m, "gold_diff": _diff(a[2], b[2]), "xp_diff": _diff(a[3], b[3]),
                                    "cs_diff": _diff(a[4], b[4]), "level_diff": _diff(a[5], b[5])})
            if out["rows"]:
                out["source"] = "client"
        if not out["rows"] and role:
            tl = _list(_dict(_dict(record).get("scoreboard")).get("timeline"))
            dur = _duration(record)
            for m in LANE_MINUTES:
                if dur and m * 60.0 > dur + 5.0:
                    continue
                best, best_dt = None, FRAME_TOL_S + 1
                for s in tl:
                    if not isinstance(s, list) or len(s) < 3 or not isinstance(s[2], dict):
                        continue
                    gt = _f(s[0])
                    v = s[2].get(role)
                    if gt is None or not isinstance(v, list) or len(v) < 3:
                        continue
                    if abs(gt - m * 60.0) < best_dt:
                        best, best_dt = v, abs(gt - m * 60.0)
                if best is not None:
                    out["rows"].append({"minute": m, "gold_diff": _f(best[0]), "xp_diff": None,
                                        "cs_diff": _f(best[1]), "level_diff": _f(best[2])})
            if out["rows"]:
                out["source"] = "tab"
                opp = next((p for p in _list(_dict(record).get("roster")) if isinstance(p, dict)
                            and _str(p.get("team")).upper() not in ("", _my_team(record))
                            and _str(p.get("position")).upper() == role), None)
                out["opponent"] = _str(_dict(opp).get("name") or _dict(opp).get("alias")) or None
        out["verdict"] = _lane_verdict(out["rows"], role)
    except Exception:
        log.exception("lane_diffs failed")
    return out


def _lane_verdict(rows: list[dict], role: str) -> str:
    if not rows:
        return ""
    r = rows[-1]
    gd, cd = _f(r.get("gold_diff")), _f(r.get("cs_diff"))
    when = f"à {r['minute']} min"
    if gd is None:
        return ""
    farm_role = role not in ("UTILITY",)
    if gd >= 500:
        return (f"Voie gagnée : {_num(gd, 0, True)} PO {when}. Convertis l'avance : pousse ta vague puis "
                "va aider sur le prochain objectif au lieu de rester en voie.")
    if gd <= -500:
        if farm_role and cd is not None and cd <= -15:
            return (f"Voie perdue : {_num(gd, 0, True)} PO {when}, dont {_num(-cd, 0)} sbires de retard. "
                    "Le retard vient du farm : achève chaque sbire, même sous ta tour.")
        return (f"Voie perdue : {_num(gd, 0, True)} PO {when}. Le retard vient surtout des morts : joue "
                "plus prudent avant le niveau 6 et recule quand le jungler adverse n'est pas visible.")
    tail = ""
    if farm_role and cd is not None and cd <= -10:
        tail = f" Mais {_num(-cd, 0)} sbires de retard : c'est là que tu peux gagner ta voie."
    return f"Voie égale : {_num(gd, 0, True)} PO {when}.{tail}"


# ======================================================================================
# 15. item timings + unspent gold
# ======================================================================================
def _legendary_fn() -> Any:
    try:
        from treeaicoach.itemization import load_items  # noqa: PLC0415

        items = load_items()
    except Exception:
        items = {}
    if items:
        return lambda i: (it := items.get(i)) is not None and it.kind == "legendary"
    return lambda i: False


def item_timing(record: Any, bracket: str = DEFAULT_BRACKET, is_legendary: Any = None) -> dict[str, Any]:
    """``{"first_min", "second_min", "first_target", "second_target", "bracket", "unspent_avg",
    "idle_min", "peak_gold", "peak_time", "lines"}`` from the recorded snapshots (inventory + gold)."""
    out: dict[str, Any] = {"first_min": None, "second_min": None, "first_target": None, "second_target": None,
                           "bracket": BRACKET_LABEL.get(bracket, bracket), "unspent_avg": None, "idle_min": None,
                           "peak_gold": None, "peak_time": None, "lines": []}
    try:
        snaps = [s for s in _list(_dict(record).get("snapshots")) if isinstance(s, dict)
                 and _f(s.get("game_time")) is not None]
        if len(snaps) < 3:
            return out
        snaps.sort(key=lambda s: float(s["game_time"]))
        legend = is_legendary or _legendary_fn()
        role = _position(record)
        tgt = ITEM_TARGETS.get(bracket, ITEM_TARGETS[DEFAULT_BRACKET]).get(role or "TOP", (13.0, 21.0))
        out["first_target"], out["second_target"] = tgt
        unspent, alive_s, idle_s = 0.0, 0.0, 0.0
        peak = (0.0, 0.0)
        late_start = float(snaps[0]["game_time"]) > LATE_START_S     # app started mid-game
        for i, s in enumerate(snaps):
            gt = float(s["game_time"])
            items = [int(x) for x in _list(s.get("items")) if isinstance(x, (int, float)) and int(x) > 0]
            n_leg = sum(1 for x in items if legend(x))
            if i == 0 and late_start:          # already bought before the recording: timing unknown
                out["first_min"] = "?" if n_leg >= 1 else None
                out["second_min"] = "?" if n_leg >= 2 else None
            if n_leg >= 1 and out["first_min"] is None:
                out["first_min"] = round(gt / 60.0, 1)
            if n_leg >= 2 and out["second_min"] is None:
                out["second_min"] = round(gt / 60.0, 1)
            if s.get("is_dead") or gt < UNSPENT_SKIP_S:
                continue
            nxt = float(snaps[i + 1]["game_time"]) if i + 1 < len(snaps) else gt + 10.0
            dt = min(15.0, max(0.0, nxt - gt))
            g = _f(s.get("gold")) or 0.0
            unspent += g * dt
            alive_s += dt
            if g >= IDLE_GOLD:
                idle_s += dt
            if g > peak[0]:
                peak = (g, gt)
        if alive_s > 0:
            out["unspent_avg"] = round(unspent / alive_s)
            out["idle_min"] = round(idle_s / 60.0, 1)
        if peak[0] > 0:
            out["peak_gold"], out["peak_time"] = int(peak[0]), clock(peak[1], "?", rounded=True)
        for k in ("first_min", "second_min"):
            if out[k] == "?":
                out[k] = None
                out["unknown_" + k] = True
        out["lines"] = _item_lines(out, _duration(record) or float(snaps[-1]["game_time"]))
    except Exception:
        log.exception("item_timing failed")
    return out


def _item_lines(d: dict, duration_s: float | None = None) -> list[str]:
    lines = []
    f1, t1, f2, t2 = d.get("first_min"), d.get("first_target"), d.get("second_min"), d.get("second_target")
    br = d.get("bracket")
    mins = (duration_s or 0.0) / 60.0
    if f1 is not None and t1 is not None:
        late = f1 - t1
        if late > 1.0:
            lines.append(f"1er objet légendaire à {_num(f1, 1)} min, {_num(late, 1)} min plus tard que la cible "
                         f"{br} ({_num(t1, 0)} min). Plus de sbires et des retours plus tôt.")
        else:
            lines.append(f"1er objet légendaire à {_num(f1, 1)} min : dans le rythme (cible {br} {_num(t1, 0)} min).")
    elif t1 is not None and not d.get("unknown_first_min") and mins >= t1 + 1.0:
        # (a game over before the target - a remake, a surrender at 15 - is not "no item")
        lines.append(f"Aucun objet légendaire terminé (cible {br} : {_num(t1, 0)} min).")
    if f2 is not None and t2 is not None and f2 - t2 > 1.5:
        lines.append(f"2e objet à {_num(f2, 1)} min (cible {_num(t2, 0)} min).")
    idle = _f(d.get("idle_min"))
    if idle is not None and idle >= 2.0:
        lines.append(f"Or qui dort : {_num(idle, 1)} min en vie avec plus de {IDLE_GOLD} PO en poche "
                     f"(pic {d.get('peak_gold')} PO à {d.get('peak_time')}). Rentre acheter dès que tu peux "
                     "finir un composant.")
    return lines


# ======================================================================================
# 19. turning point (estimated win probability from the recorded events)
# ======================================================================================
def _name_teams(record: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in _list(_dict(record).get("roster")):
        if not isinstance(p, dict):
            continue
        team = _str(p.get("team")).upper()
        if team not in ("ORDER", "CHAOS"):
            continue
        for k in ("alias", "name", "riot_id", "summoner_name", "riot_id_game_name"):
            key = _key(p.get(k))
            if key:
                out[key] = team
    return out


def _structure_owner(name: Any) -> str | None:
    from treeaicoach.phase import structure_owner  # noqa: PLC0415

    return structure_owner(_str(name))


def _my_keys(record: Any) -> set[str]:
    m = _meta(record)
    keys = {_key(m.get(k)) for k in ("riot_id", "summoner_name", "champion", "champion_name")}
    for p in _list(_dict(record).get("roster")):
        if isinstance(p, dict) and p.get("is_me"):
            keys |= {_key(p.get(k)) for k in ("alias", "name", "riot_id", "summoner_name")}
    return {k for k in keys if k}


def _other(team: str) -> str:
    return "CHAOS" if team == "ORDER" else "ORDER"


def win_prob(lead: float, t: float) -> float:
    """Estimated win probability of a team ``lead`` gold-equivalent ahead at game time ``t`` (s)."""
    scale = 1200.0 + 220.0 * max(0.0, t) / 60.0
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, lead / scale))))


def _swings(record: Any) -> list[dict]:
    """Scored events: ``{t, team (who gains), value, kind, victim_me, obj}``."""
    teams = _name_teams(record)
    mine = _my_keys(record)
    out = []
    for e in _list(_dict(record).get("events")):
        if not isinstance(e, dict):
            continue
        name, t = _str(e.get("EventName")), _f(e.get("EventTime"))
        if t is None:
            continue
        killer_team = teams.get(_key(e.get("KillerName"))) or _structure_owner(e.get("KillerName"))
        if killer_team is None:
            killer_team = next((teams[k] for k in (_key(a) for a in _list(e.get("Assisters"))) if k in teams), None)
        if name == "ChampionKill":
            vt = teams.get(_key(e.get("VictimName")))
            if vt is None:
                continue
            out.append({"t": t, "team": _other(vt), "value": EV_KILL + EV_KILL_PER_MIN * t / 60.0, "kind": "kill",
                        "victim_me": _key(e.get("VictimName")) in mine})
        elif name in ("TurretKilled", "InhibKilled"):
            owner = _structure_owner(e.get(name))
            gain = _other(owner) if owner else killer_team
            if gain:
                out.append({"t": t, "team": gain, "value": EV_VALUE[name], "kind": name, "victim_me": False})
        elif name in EV_VALUE and killer_team:
            v = EV_VALUE[name]
            if name == "DragonKill" and _str(e.get("DragonType")).lower() == "elder":
                v, name = EV_ELDER, "ElderKill"
            out.append({"t": t, "team": killer_team, "value": v, "kind": name, "victim_me": False})
    out.sort(key=lambda x: x["t"])
    return out


_OBJ_FR = {"TurretKilled": ("une tour", "tour"), "InhibKilled": ("un inhibiteur", "inhibiteur"),
           "DragonKill": ("le dragon", "dragon"), "ElderKill": ("le dragon ancestral", "dragon ancestral"),
           "BaronKill": ("le Baron", "Baron"), "HeraldKill": ("le Héraut", "Héraut"),
           "HordeKill": ("les larves", "larves")}


def turning_point(record: Any) -> dict[str, Any] | None:
    """``{"time", "game_time", "before", "after", "text", "ours"}`` of the biggest win-probability
    swing (towards the final result when there is one), or None without usable events."""
    try:
        team = _my_team(record)
        if team not in ("ORDER", "CHAOS"):
            return None
        evs = _swings(record)
        if not evs:
            return None
        clusters: list[list[dict]] = []
        for e in evs:
            if clusters and e["t"] - clusters[-1][-1]["t"] <= CLUSTER_GAP_S:
                clusters[-1].append(e)
            else:
                clusters.append([e])
        lead = 0.0
        scored = []
        for c in clusters:
            t0 = c[0]["t"]
            p0 = win_prob(lead, t0)
            for e in c:
                lead += e["value"] if e["team"] == team else -e["value"]
            p1 = win_prob(lead, c[-1]["t"])
            scored.append((p1 - p0, p0, p1, c))
        result = _str(_dict(record).get("result") or _dict(_dict(record).get("summary")).get("result"))
        want = 1 if result == "Win" else -1 if result == "Lose" else 0
        cand = [s for s in scored if want == 0 or s[0] * want > 0] or scored
        d, p0, p1, c = max(cand, key=lambda s: abs(s[0]))
        if abs(d) < 0.03:
            return None
        ours = d > 0
        t = c[0]["t"]
        ally_k = sum(1 for e in c if e["kind"] == "kill" and e["team"] == team)
        enemy_k = sum(1 for e in c if e["kind"] == "kill" and e["team"] != team)
        me_dead = any(e["victim_me"] for e in c)
        objs = [e for e in c if e["kind"] != "kill"]
        what = []
        if ally_k or enemy_k:
            if ally_k > enemy_k:
                what.append(f"combat gagné {ally_k} à {enemy_k}")
            elif enemy_k > ally_k:
                what.append(f"combat perdu {ally_k} à {enemy_k}" + (" (tu es mort dedans)" if me_dead else ""))
            else:
                what.append(f"échange {ally_k} kill{'s' if ally_k > 1 else ''} partout")
        best_obj = max(objs, key=lambda e: e["value"]) if objs else None
        if best_obj is not None:
            noun = _OBJ_FR.get(best_obj["kind"], ("un objectif", "objectif"))[0]
            what.append(f"{noun} pour {'vous' if best_obj['team'] == team else 'l’ennemi'}")
        desc = " puis ".join(what) or "série d'événements"
        lesson = ""
        if not ours:
            if best_obj is not None and best_obj["kind"] in ("BaronKill", "ElderKill", "DragonKill"):
                lesson = " Avant un objectif, regroupez-vous et posez la vision une minute avant."
            elif enemy_k:
                lesson = " Ne te bats pas en infériorité numérique : attends ton équipe."
        else:
            lesson = " C'est ce genre de combat groupé qui gagne les parties."
        tm = clock(t, "?", rounded=True)
        text = (f"Tournant de la partie à {tm} : {desc}. Chances de victoire estimées "
                f"{_pct(p0)} → {_pct(p1)}.{lesson}")
        return {"time": tm, "game_time": round(t, 1), "before": round(p0, 3), "after": round(p1, 3),
                "ours": ours, "text": text.replace("’", "'")}
    except Exception:
        log.exception("turning_point failed")
        return None


# ======================================================================================
# per-game features (cached by progress.collect)
# ======================================================================================
def game_features(record: Any, analysis: Any = None) -> dict[str, Any]:
    """Compact numbers of one game for the multi-game views. Champion aliases only, no names."""
    out: dict[str, Any] = {}
    try:
        rec = _dict(record)
        a = _dict(analysis)
        s = _dict(a.get("summary"))
        team = _my_team(rec)
        role = _position(rec)
        out["team"] = team
        out["game_mode"] = _str(_meta(rec).get("game_mode") or s.get("game_mode")).upper()
        out["kills"] = int(_f(s.get("kills")) or 0)
        out["assists"] = int(_f(s.get("assists")) or 0)
        out["kp"] = _f(s.get("kill_participation"))
        opp = next((e for e in _list(s.get("enemies")) if isinstance(e, dict)
                    and _str(e.get("position")).upper() == role and role), None)
        out["opponent"] = _str(_dict(opp).get("alias")) or None
        out["opponent_name"] = _str(_dict(opp).get("name")) or out["opponent"]
        # gold per minute (farm axis of a support): the final Tab estimate
        me_sb = next((p for p in _list(_dict(_dict(rec.get("scoreboard")).get("final")).get("players"))
                      if isinstance(p, dict) and p.get("side") == "self"), None)
        dur = _duration(rec)
        g = _f(_dict(me_sb).get("est_gold")) or _f(_dict(me_sb).get("item_gold"))
        out["gold_per_min"] = round(g / (dur / 60.0), 1) if g and dur >= 60 else None
        op = _dict(a.get("objective_presence"))
        out["obj_presence"] = _f(op.get("percent"))
        # time in the enemy half (aggression axis)
        pts = [p for p in _list(rec.get("my_positions")) if isinstance(p, list) and len(p) >= 3]
        if pts and team:
            n_in = sum(1 for p in pts if _f(p[1]) is not None and _f(p[2]) is not None
                       and _enemy_half(float(p[1]), float(p[2]), team))
            out["enemy_half"] = round(n_in / len(pts), 3)
        else:
            out["enemy_half"] = None
        # deaths: exact client positions when known, else the recorded ones
        deaths: list[list[Any]] = []
        ta = _dict(a.get("truth"))
        exact = ta.get("deaths") if ta.get("available") else None
        by_t = {round(_f(d.get("game_time")) or -1): d for d in _list(a.get("deaths")) if isinstance(d, dict)}
        src = exact if isinstance(exact, list) and exact else _list(a.get("deaths"))
        for d in src:
            if not isinstance(d, dict) or not isinstance(d.get("uv"), list) or len(d["uv"]) < 2:
                continue
            u, v = _f(d["uv"][0]), _f(d["uv"][1])
            if u is None or v is None:
                continue
            gt = _f(d.get("game_time")) or 0.0
            ctx = by_t.get(round(gt)) or next((x for x in by_t.values()
                                               if abs((_f(x.get("game_time")) or -99) - gt) <= 6), {}) or d
            deaths.append([round(u, 3), round(v, 3), round(gt),
                           int(bool(ctx.get("jungler_unseen"))), int(bool(ctx.get("warned"))),
                           int((_f(ctx.get("enemy_count")) or 0) >= 2)])
        out["death_pts"] = deaths[:30]
        it = item_timing(rec)
        out["first_item_min"] = it.get("first_min")
        out["unspent_avg"] = it.get("unspent_avg")
    except Exception:
        log.exception("game_features failed")
    return out


# ======================================================================================
# 16. deaths over all games
# ======================================================================================
def _death_zone(u: float, v: float, team: str) -> tuple[str, str]:
    """(key, French label) of a death position: zone + "côté ennemi" for lanes past the river."""
    try:
        from treeaicoach import geometry  # noqa: PLC0415

        z = geometry.classify_zone(u, v)
        name = geometry.zone_name_fr(z, team or None) or "zone inconnue"
        lane = geometry.lane_of(z)
        if lane:
            far = _enemy_half(u, v, team) if team else False
            return f"{z.value}:{int(far)}", f"{name}, {'côté ennemi' if far else 'près de ta tour'}"
        return str(z.value), name
    except Exception:
        return "?", "zone inconnue"


def death_zones(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """``{"total", "games", "points": [(u, v)], "zones": [{key, label, count, share}], "top", "sentence"}``."""
    out: dict[str, Any] = {"total": 0, "games": 0, "points": [], "zones": [], "top": None, "sentence": ""}
    try:
        groups: dict[str, dict[str, Any]] = {}
        games = 0
        for r in rows:
            pts = _list(r.get("death_pts"))
            if r.get("death_pts") is not None:
                games += 1
            team = _str(r.get("team")).upper()
            for p in pts:
                if not isinstance(p, list) or len(p) < 2:
                    continue
                u, v = _f(p[0]), _f(p[1])
                if u is None or v is None:
                    continue
                # map seen from the player's side: own base always bottom-left (CHAOS games rotated)
                out["points"].append((1.0 - u, 1.0 - v) if team == "CHAOS" else (u, v))
                key, label = _death_zone(u, v, team)
                g = groups.setdefault(key, {"key": key, "label": label, "count": 0, "unseen": 0, "unwarned": 0,
                                            "outnumbered": 0, "early": 0})
                g["count"] += 1
                g["unseen"] += int(len(p) > 3 and bool(p[3]))
                g["unwarned"] += int(len(p) > 4 and not p[4])
                g["outnumbered"] += int(len(p) > 5 and bool(p[5]))
                g["early"] += int(len(p) > 2 and (_f(p[2]) or 0) < 840)
        total = len(out["points"])
        out["total"], out["games"] = total, games
        if not total:
            return out
        zs = sorted(groups.values(), key=lambda g: -g["count"])
        for g in zs:
            g["share"] = round(g["count"] / total, 3)
        out["zones"] = zs
        top = zs[0]
        out["top"] = top
        if total >= 3 and top["count"] >= 2:
            ctx = []
            c = top["count"]
            if top["unseen"] >= max(2, c / 2):
                ctx.append("jungler ennemi invisible")
            if top["outnumbered"] >= max(2, c / 2):
                ctx.append("à plusieurs contre toi")
            if top["early"] >= max(2, 0.6 * c):
                ctx.append("avant 14 min")
            tail = f", souvent {' et '.join(ctx)}" if ctx else ""
            out["sentence"] = (f"{round(100 * top['share'])} % de tes morts ({c} sur {total}, {games} parties) : "
                               f"{top['label']}{tail}.")
    except Exception:
        log.exception("death_zones failed")
    return out


# ======================================================================================
# 17. six-axis profile
# ======================================================================================
def _clamp(x: float) -> float:
    return max(0.0, min(100.0, x))


def axis_scores(r: Mapping[str, Any]) -> dict[str, float | None]:
    """0-100 per axis for one game row (None = unknown)."""
    role = _str(r.get("position")).upper()
    dur = _f(r.get("duration")) or 0.0
    mins = dur / 60.0 if dur > 0 else None
    out: dict[str, float | None] = {k: None for k, _ in AXES}
    if role == "UTILITY":
        gpm = _f(r.get("gold_per_min"))
        out["farm"] = _clamp(100 * gpm / SUPPORT_GOLD_TARGET) if gpm is not None else None
    else:
        cs = _f(r.get("cs_per_min"))
        out["farm"] = _clamp(100 * cs / CS_TARGET.get(role, 7.0)) if cs is not None else None
    vis = _f(r.get("vision_per_min"))
    if vis is not None:
        out["vision"] = _clamp(100 * vis / VISION_TARGET.get(role, VISION_TARGET_DEFAULT))
    kp = _f(r.get("kp"))
    if kp is not None:
        out["fight"] = _clamp(100 * kp / 0.65)
    d = _f(r.get("deaths"))
    if d is not None and mins:
        out["survival"] = _clamp(100 - 33.0 * d * 10.0 / mins)
    op = _f(r.get("obj_presence"))
    if op is not None:
        out["objectives"] = _clamp(op)
    eh = _f(r.get("enemy_half"))
    if eh is not None and mins:
        k10 = ((_f(r.get("kills")) or 0) + 0.5 * (_f(r.get("assists")) or 0)) * 10.0 / mins
        out["aggression"] = _clamp(60 * min(1.0, eh / 0.35) + 40 * min(1.0, k10 / 3.0))
    return out


#: Summoner's Rift modes with the normal economy (Swiftplay is a normal draft-less SR game)
RIFT_MODES = ("", "CLASSIC", "SWIFTPLAY")


def _classic(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [r for r in rows if isinstance(r, Mapping) and _str(r.get("game_mode")).upper() in RIFT_MODES
            and (_f(r.get("duration")) or 0) >= MIN_GAME_S]


def profile(rows: Sequence[Mapping[str, Any]], last: int = 10) -> dict[str, Any]:
    """``{"games", "overall", "axes": [{key, label, value, values, trend}], "best", "worst", "sentence"}``.
    ``trend``: mean of the last 3 games minus the mean of the games before (within the last ``last``)."""
    out: dict[str, Any] = {"games": 0, "overall": None, "axes": [], "best": None, "worst": None, "sentence": ""}
    try:
        rs = _classic(rows)[-max(1, int(last)):]
        out["games"] = len(rs)
        if not rs:
            return out
        per = [axis_scores(r) for r in rs]
        vals_all = []
        for key, label in AXES:
            values = [p[key] for p in per]
            known = [v for v in values if v is not None]
            value = round(sum(known) / len(known)) if known else None
            trend = None
            if len(known) >= 5:
                recent, before = known[-3:], known[:-3]
                trend = round(sum(recent) / 3 - sum(before) / len(before))
            out["axes"].append({"key": key, "label": label, "value": value,
                                "values": [None if v is None else round(v) for v in values], "trend": trend})
            if value is not None:
                vals_all.append(value)
        if vals_all:
            out["overall"] = round(sum(vals_all) / len(vals_all))
        known_axes = [x for x in out["axes"] if x["value"] is not None]
        if known_axes:
            best = max(known_axes, key=lambda x: x["value"])
            worst = min(known_axes, key=lambda x: x["value"])
            out["best"], out["worst"] = best["label"], worst["label"]
            moving = [x for x in known_axes if x["trend"] is not None and abs(x["trend"]) >= 8]
            mv = ""
            if moving:
                m = max(moving, key=lambda x: abs(x["trend"]))
                mv = f" {m['label']} : {'en hausse' if m['trend'] > 0 else 'en baisse'} ({_num(m['trend'], 0, True)}) sur tes 3 dernières parties."
            out["sentence"] = (f"Score global {out['overall']}/100 sur {len(rs)} parties. Point fort : "
                               f"{best['label'].lower()} ({best['value']}). À travailler : {worst['label'].lower()} "
                               f"({worst['value']}).{mv}")
    except Exception:
        log.exception("profile failed")
    return out


# ======================================================================================
# 18. champion pool
# ======================================================================================
def champion_pool(rows: Sequence[Mapping[str, Any]], min_matchup: int = 2) -> dict[str, Any]:
    """``{"champions": [{champion, name, games, wins, winrate, kda, matchups, line}], "advice"}``."""
    out: dict[str, Any] = {"champions": [], "advice": ""}
    try:
        champs: dict[str, dict[str, Any]] = {}
        for r in _classic(rows):
            res = _str(r.get("result"))
            if res not in ("Win", "Lose"):
                continue
            c = _str(r.get("champion"))
            if not c:
                continue
            e = champs.setdefault(c, {"champion": c, "name": _str(r.get("champion_name")) or c, "games": 0,
                                      "wins": 0, "k": 0.0, "d": 0.0, "a": 0.0, "mu": {}})
            e["games"] += 1
            e["wins"] += int(res == "Win")
            e["k"] += _f(r.get("kills")) or 0
            e["d"] += _f(r.get("deaths")) or 0
            e["a"] += _f(r.get("assists")) or 0
            opp = _str(r.get("opponent"))
            if opp:
                m = e["mu"].setdefault(opp, {"opponent": opp, "name": _str(r.get("opponent_name")) or opp,
                                             "games": 0, "wins": 0})
                m["games"] += 1
                m["wins"] += int(res == "Win")
        rows_out = []
        for e in sorted(champs.values(), key=lambda x: (-x["games"], -x["wins"])):
            wr = e["wins"] / e["games"]
            kda = (e["k"] + e["a"]) / max(1.0, e["d"])
            mus = sorted(e["mu"].values(), key=lambda m: (-m["games"], m["wins"]))
            for m in mus:
                m["winrate"] = round(m["wins"] / m["games"], 3)
            bad = [m for m in mus if m["games"] >= min_matchup and m["winrate"] <= 0.34]
            good = [m for m in mus if m["games"] >= min_matchup and m["winrate"] >= 0.66]
            line = (f"{e['name']} : {round(100 * wr)} % sur {e['games']} partie{'s' if e['games'] > 1 else ''}, "
                    f"KDA {_num(kda, 1)}")
            if bad:
                b = bad[0]
                line += f", évite contre {b['name']} ({b['wins']}/{b['games']})"
            elif good:
                g = good[0]
                line += f", fort contre {g['name']} ({g['wins']}/{g['games']})"
            rows_out.append({"champion": e["champion"], "name": e["name"], "games": e["games"], "wins": e["wins"],
                             "winrate": round(wr, 3), "kda": round(kda, 2), "matchups": mus, "line": line})
        out["champions"] = rows_out
        solid = [c for c in rows_out if c["games"] >= 5]
        if len(solid) >= 2:
            best = max(solid, key=lambda c: (c["winrate"], c["kda"]))
            worst = min(solid, key=lambda c: (c["winrate"], c["kda"]))
            if best["winrate"] - worst["winrate"] >= 0.15:
                out["advice"] = (f"Joue {best['name']} en priorité ({round(100 * best['winrate'])} %) ; "
                                 f"{worst['name']} te coûte des parties ({round(100 * worst['winrate'])} %).")
        elif len(solid) == 1:
            out["advice"] = f"{solid[0]['name']} est ton champion principal : reste dessus pour progresser."
    except Exception:
        log.exception("champion_pool failed")
    return out


# ======================================================================================
# 20. team recap (Discord text channel)
# ======================================================================================
def _ally_axes(p: Mapping[str, Any], minutes: float, team_kills: int) -> list[tuple[float, str, str, str]]:
    """(score 0..~1.5, key, stat text, fix text) per measurable axis of one player of the Tab scoreboard."""
    role = _str(p.get("role")).upper()
    out = []
    k, d, a = (int(_f(p.get(x)) or 0) for x in ("kills", "deaths", "assists"))
    if role not in ("UTILITY",) and _f(p.get("cs_per_min")) is not None:
        cs = float(_f(p.get("cs_per_min")) or 0.0)
        tgt = CS_TARGET.get(role, 7.0)
        out.append((cs / tgt, "farm", f"{_num(cs, 1)} CS/min",
                    f"farm à {_num(cs, 1)} CS/min, vise {_num(tgt, 0)} : achève les sbires sous la tour"))
    ws = _f(p.get("ward_score"))
    if ws is not None and minutes > 0:
        vpm = ws / minutes
        tgt = VISION_TARGET.get(role, VISION_TARGET_DEFAULT)
        out.append((vpm / tgt, "vision", f"{_num(vpm, 1)} de vision/min",
                    "pose plus de balises : une balise de contrôle à chaque retour"))
    if team_kills > 0:
        kp = (k + a) / team_kills
        out.append((kp / 0.65, "fight", f"{round(100 * kp)} % de participation aux kills",
                    f"seulement {round(100 * kp)} % des kills : rejoins l'équipe sur les objectifs"))
    if minutes > 0:
        d10 = d * 10.0 / minutes
        out.append((max(0.0, 1.4 - 0.4 * d10), "survival",
                    "aucune mort" if d == 0 else f"seulement {d} mort{'s' if d > 1 else ''}" if d <= 3
                    else f"{d} morts",
                    f"{d} morts : recule quand le jungler adverse n'est pas visible"))
    return out


def team_recap(record: Any, friends: Mapping[str, str], max_len: int = 1900) -> str:
    """Discord message: one line per friend (``{champion alias: Discord display name}``) found on my
    team in the record's final Tab scoreboard: their key stat and one fix. ``""`` when nothing to say."""
    try:
        rec = _dict(record)
        final = _dict(_dict(rec.get("scoreboard")).get("final"))
        players = [p for p in _list(final.get("players")) if isinstance(p, dict)]
        if not players or not friends:
            return ""
        minutes = _duration(rec) / 60.0
        team_kills = int(_f(final.get("ally_kills")) or 0)
        by_alias = {_key(p.get("alias")): p for p in players if p.get("side") in ("ally", "self")}
        lines = []
        for alias, who in friends.items():
            p = by_alias.get(_key(alias))
            if p is None:
                continue
            axes = _ally_axes(p, minutes, team_kills)
            if not axes:
                continue
            best = max(axes, key=lambda x: x[0])
            worst = min(axes, key=lambda x: x[0])
            name = _str(who).replace("@", "@​").replace("`", "'")[:32] or _str(p.get("name"))
            champ = _str(p.get("name") or p.get("alias"))
            k, d, a = (int(_f(p.get(x)) or 0) for x in ("kills", "deaths", "assists"))
            fix = worst[3] if worst[0] < 0.85 and worst is not best else "rien de gros à corriger, continue"
            lines.append(f"• **{name}** ({champ}, {k}/{d}/{a}) : {best[2]} · à corriger : {fix}.")
        if not lines:
            return ""
        res = {"Win": "Victoire", "Lose": "Défaite"}.get(_str(rec.get("result")), "Partie terminée")
        head = f"**TreeAI Coach · {res} en {round(minutes)} min**"
        tp = turning_point(rec)
        body = "\n".join([head, *lines] + ([f"_{tp['text']}_"] if tp else []))
        return body[:max_len]
    except Exception:
        log.exception("team_recap failed")
        return ""


# ======================================================================================
# 21. farm gap, time dead, level race (own data: snapshots, Tab timeline, client truth)
# ======================================================================================
GOLD_PER_CS = 20.0               # average wave minion (3 melee 21, 3 casters 14, a siege every 3 waves)
FARM_LINE_GOLD = 600.0           # below this the farm gap is not worth a line
CS10_FACTOR = 8.5                # 10-min CS target = CS/min target x 8.5 (minions from 1:05, slow first waves)
#: post-game bracket -> live_edge.CS_TARGETS bracket (the live line and the report use the same targets)
_LIVE_BRACKET = {"debutant": "low", "argent-or": "mid", "platine+": ""}
LANE_ROLES = ("TOP", "MIDDLE", "BOTTOM", "UTILITY")


def _snaps(record: Any) -> list[dict]:
    snaps = [s for s in _list(_dict(record).get("snapshots")) if isinstance(s, dict) and _f(s.get("game_time")) is not None]
    return sorted(snaps, key=lambda s: float(s["game_time"]))


def _gold_fr(x: float) -> str:
    return f"{int(round(x / 100.0) * 100):,}".replace(",", " ")


def farm_gap(record: Any, bracket: str = DEFAULT_BRACKET, truth: Any = None) -> dict[str, Any]:
    """``{"cs", "cs_per_min", "target", "missed_cs", "missed_gold", "cs10", "cs10_target", "line"}``: the
    minions left on the map against the CS/min target of the bracket (the live line's target), in
    gold. Not for the support (no farm target). ``{}`` without data. Never raises."""
    try:
        role = _position(record)
        if role in ("", "UTILITY"):
            return {}
        from treeaicoach.live_edge import CS_TARGETS  # noqa: PLC0415

        target = CS_TARGETS.get(_LIVE_BRACKET.get(bracket, "low"), CS_TARGETS["low"]).get(role)
        snaps = _snaps(record)
        dur = _duration(record)
        if target is None or not snaps or dur < MIN_GAME_S:
            return {}
        cs = _f(snaps[-1].get("cs"))
        if cs is None:
            cs = _f(_dict(_dict(record).get("summary")).get("cs"))
        if cs is None:
            return {}
        mins = dur / 60.0
        missed = max(0.0, target * mins - cs)
        out: dict[str, Any] = {"cs": int(cs), "cs_per_min": round(cs / mins, 1), "target": target,
                               "missed_cs": int(round(missed)), "missed_gold": int(round(missed * GOLD_PER_CS)),
                               "cs10": None, "cs10_target": int(round(target * CS10_FACTOR)), "line": ""}
        if float(snaps[0]["game_time"]) <= LATE_START_S:
            s10 = min(snaps, key=lambda s: abs(float(s["game_time"]) - 600.0))
            if abs(float(s10["game_time"]) - 600.0) <= 30.0 and _f(s10.get("cs")) is not None:
                out["cs10"] = int(_f(s10.get("cs")) or 0)
        if out["missed_gold"] >= FARM_LINE_GOLD:
            what = " (un objet entier)" if out["missed_gold"] >= 2600 else ""
            line = (f"Farm : {_num(out['cs_per_min'], 1)} CS/min pour une cible de {_num(target, 1)} : environ "
                    f"{out['missed_cs']} sbires, soit {_gold_fr(out['missed_gold'])} PO, laissés sur la carte{what}.")
            if out["cs10"] is not None and out["cs10"] < out["cs10_target"]:
                line += f" À 10 min : {out['cs10']} sbires (cible {out['cs10_target']})."
            out["line"] = line
        return out
    except Exception:
        log.exception("farm_gap failed")
        return {}


def time_dead(record: Any) -> dict[str, Any]:
    """``{"dead_s", "share", "deaths", "farm_lost_cs", "line"}``: the time spent dead (recorded
    snapshots, ``is_dead``) and the farm it cost (my own alive CS rate). ``{}`` without data."""
    try:
        snaps = _snaps(record)
        if len(snaps) < 3:
            return {}
        span = float(snaps[-1]["game_time"]) - float(snaps[0]["game_time"])
        if span < MIN_GAME_S:
            return {}
        dead_s = 0.0
        for i, s in enumerate(snaps[:-1]):
            if s.get("is_dead"):
                dead_s += min(15.0, max(0.0, float(snaps[i + 1]["game_time"]) - float(s["game_time"])))
        deaths = int(_f(snaps[-1].get("deaths")) or _f(_dict(_dict(record).get("summary")).get("deaths")) or 0)
        share = dead_s / span
        role = _position(record)
        cs0, cs1 = _f(snaps[0].get("cs")) or 0.0, _f(snaps[-1].get("cs")) or 0.0
        alive_min = max(1.0, (span - dead_s) / 60.0)
        lost = 0 if role in ("", "UTILITY") else int(round(max(0.0, cs1 - cs0) / alive_min * dead_s / 60.0))
        out = {"dead_s": round(dead_s), "share": round(share, 3), "deaths": deaths, "farm_lost_cs": lost, "line": ""}
        if dead_s >= 60.0 and (share >= 0.06 or deaths >= 4):
            tail = (f" : environ {lost} sbires ({_gold_fr(lost * GOLD_PER_CS)} PO) de farm perdus pendant ce temps"
                    if lost >= 10 else "")
            out["line"] = (f"Temps mort : {clock(dead_s, '?', rounded=True)} au total ({round(100 * share)} % de la "
                           f"partie, {deaths} mort{'s' if deaths > 1 else ''}){tail}.")
        return out
    except Exception:
        log.exception("time_dead failed")
        return {}


def _level_rows(record: Any, truth: Any) -> tuple[list[tuple[float, int, int]], str | None]:
    """``[(game time, my level, his level)]`` up to 15:00 and the opponent name: the client truth
    (exact, per minute) else the Tab timeline (exact level diff, ~1 min) + my snapshots."""
    t = truth if isinstance(truth, dict) else {}
    rows: list[tuple[float, int, int]] = []
    if t.get("frames") and t.get("me") is not None and t.get("lane_opponent") is not None:
        me, opp = str(t["me"]), str(t["lane_opponent"])
        for fr in _list(t.get("frames")):
            if not isinstance(fr, list) or len(fr) < 2 or not isinstance(fr[1], dict) or _f(fr[0]) is None:
                continue
            a, b = fr[1].get(me), fr[1].get(opp)
            if float(fr[0]) <= 15 * 60 + 5 and isinstance(a, list) and isinstance(b, list) and len(a) >= 6 and len(b) >= 6:
                rows.append((float(fr[0]), int(_f(a[5]) or 1), int(_f(b[5]) or 1)))
        parts = {_str(p.get("id")): p for p in _list(t.get("participants")) if isinstance(p, dict)}
        if rows:
            return rows, _str(_dict(parts.get(opp)).get("name") or _dict(parts.get(opp)).get("alias")) or None
    role = _position(record)
    snaps = _snaps(record)
    if not snaps:
        return [], None
    for row in _list(_dict(_dict(record).get("scoreboard")).get("timeline")):
        if not isinstance(row, list) or len(row) < 3 or not isinstance(row[2], dict) or _f(row[0]) is None:
            continue
        gt = float(row[0])
        v = row[2].get(role)
        if gt > 15 * 60 + 5 or not isinstance(v, list) or len(v) < 3 or _f(v[2]) is None:
            continue
        s = min(snaps, key=lambda x: abs(float(x["game_time"]) - gt))
        if abs(float(s["game_time"]) - gt) > 15.0 or _f(s.get("level")) is None:
            continue
        mine = int(_f(s.get("level")) or 1)
        rows.append((gt, mine, mine - int(_f(v[2]) or 0)))
    opp = next((p for p in _list(_dict(record).get("roster")) if isinstance(p, dict)
                and _str(p.get("team")).upper() not in ("", _my_team(record))
                and _str(p.get("position")).upper() == role), None)
    return rows, (_str(_dict(opp).get("name") or _dict(opp).get("alias")) or None)


def level_race(record: Any, truth: Any = None) -> dict[str, Any]:
    """``{"behind": (from_min, to_min) | None, "ahead": ..., "six": (my min, his min), "line"}``: the
    level race against my lane opponent during the laning phase (levels are public: exact). One
    line: a level deficit held 3+ minutes, else him reaching level 6 a minute+ before me, else my
    own lead (the window I had). ``{}`` without data. Never raises."""
    try:
        if _position(record) not in LANE_ROLES:
            return {}
        rows, opp = _level_rows(record, truth)
        rows = sorted(r for r in rows if r[0] >= 60.0)
        if len(rows) < 4:
            return {}
        opp = opp or "ton adversaire"

        def longest(pred: Any) -> tuple[float, float] | None:
            best, start, prev = None, None, None
            for gt, a, b in rows + [(1e9, 0, 0)]:
                if gt < 1e9 and pred(a - b):
                    start = gt if start is None else start
                    prev = gt
                elif start is not None:
                    if best is None or prev - start > best[1] - best[0]:
                        best = (start, prev)
                    start = None
            return best

        behind, ahead = longest(lambda d: d <= -1), longest(lambda d: d >= 1)
        six_me = next((gt for gt, a, _b in rows if a >= 6), None)
        six_him = next((gt for gt, _a, b in rows if b >= 6), None)
        out: dict[str, Any] = {"behind": None, "ahead": None, "six": (six_me, six_him), "line": ""}
        m = lambda x: int(round(x / 60.0))  # noqa: E731
        support = _position(record) == "UTILITY"
        if behind and behind[1] - behind[0] >= 170.0:
            out["behind"] = (m(behind[0]), m(behind[1]))
            fix = ("L'XP se perd en mourant et loin de ta voie : reste près de ton tireur tant que la voie dure."
                   if support else "L'XP se perd en mourant et en rentrant avec une vague devant toi : pousse ta "
                   "vague avant de rentrer.")
            out["line"] = f"Niveaux : en retard sur {opp} de {m(behind[0])} à {m(behind[1])} min. {fix}"
        elif six_him is not None and (six_me is None or six_me - six_him >= 55.0):
            out["line"] = (f"Niveau 6 : {opp} l'a eu avant toi (vers {m(six_him)} min). Recule à ce moment-là : "
                           "son ultime change le combat.")
        elif ahead and ahead[1] - ahead[0] >= 170.0:
            out["ahead"] = (m(ahead[0]), m(ahead[1]))
            out["line"] = (f"Niveaux : en avance sur {opp} de {m(ahead[0])} à {m(ahead[1])} min. C'était ta fenêtre "
                           + ("pour engager avec ton tireur." if support else "pour attaquer et prendre ses plaques."))
        return out
    except Exception:
        log.exception("level_race failed")
        return {}


# ======================================================================================
# bundles used by report.py / the Analyse page
# ======================================================================================
def game_edge(record: Any, truth: Any = None, bracket: str = DEFAULT_BRACKET) -> dict[str, Any]:
    """Everything about the game just played (lane diffs, item timing, turning point, farm gap,
    time dead, level race). A remake (< :data:`MIN_GAME_S`) or another mode (ARAM, Arena, URF...)
    gets nothing: its numbers would only be noise next to the rift targets."""
    empty = {"lane": {"source": None, "opponent": None, "rows": [], "verdict": ""}, "items": {"lines": []},
             "turning_point": None, "farm": {}, "dead": {}, "levels": {}, "lines": []}
    try:
        mode = _str(_meta(record).get("game_mode") or _dict(_dict(record).get("summary")).get("game_mode")).upper()
        if _duration(record) < MIN_GAME_S or mode not in RIFT_MODES:
            return empty
    except Exception:
        return empty
    farm, dead, levels = farm_gap(record, bracket, truth), time_dead(record), level_race(record, truth)
    return {"lane": lane_diffs(record, truth), "items": item_timing(record, bracket),
            "turning_point": turning_point(record), "farm": farm, "dead": dead, "levels": levels,
            "lines": [x for x in (farm.get("line"), dead.get("line"), levels.get("line")) if x]}


def history_edge(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Everything over many games (death zones, six-axis profile, champion pool)."""
    rs = [r for r in rows if isinstance(r, Mapping)]
    return {"deaths": death_zones(_classic(rs)), "profile": profile(rs), "pool": champion_pool(rs),
            "games": len(rs)}


def load_history(games_dir: Any = None, last: int = 200, until: str | None = None) -> dict[str, Any]:
    """:func:`history_edge` of the recorded games (progress.collect, cached); ``until``: a record file
    name, the games recorded after it are left out (report of an older game). Never raises."""
    try:
        from treeaicoach import progress  # noqa: PLC0415

        rows = progress.collect(games_dir, last=last)
        if until:
            rows = [r for r in rows if str(r.get("file") or "") <= until]
        return history_edge(rows)
    except Exception:
        log.exception("load_history failed")
        return history_edge([])


__all__ = ["lane_diffs", "item_timing", "turning_point", "win_prob", "farm_gap", "time_dead", "level_race", "game_features", "death_zones", "profile",
           "axis_scores", "champion_pool", "team_recap", "game_edge", "history_edge", "load_history", "AXES",
           "ITEM_TARGETS"]
