"""Ground truth of a finished game (League Client timeline, see ``lcu.py``): pure functions.

* :func:`build_truth` turns the client's match + timeline JSON into a compact per-game file
  (``"schema": 1``): the 10 participants (champion alias, team, position, smite), the
  per-minute frames (normalized ``u, v`` + total gold, XP, CS, level of every player), the
  kills (time, position, killer, victim, assisters), the epic monsters and the buildings.
* :func:`analyze_truth` compares it with the TreeAI record of the same game: my exact
  deaths, the true enemy-jungler path (first 15 min, first clear side), gold / XP / CS diffs
  vs my lane opponent at 10 and 15 min, and **how reliable TreeAI was**: gank alert
  precision, missed ganks (deaths with the enemy jungler involved and no alert), how often
  the fog circle contained the true jungler position, identification accuracy of the
  minimap sightings, and a sensitivity suggestion.
* :func:`aggregate_scores` sums the per-game scores of the last games (calibration).
* :func:`truth_path_for` / :func:`save_truth` / :func:`load_truth`: ``games/truth/<stem>.truth.json``
  (a sub-folder, so that the history list never mistakes it for a game record).

Map coordinates: ``x, y`` in game units (0..14870 / 0..14980, y up) -> ``geometry.game_to_uv``.
Nothing here raises: bad input gives ``None`` / ``{"available": False}``.
"""

from __future__ import annotations

import bisect
import json
import logging
import math
from pathlib import Path
from typing import Any, Callable, Iterable

from treeaicoach import geometry

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
TRUTH_DIR_NAME = "truth"
TRUTH_SUFFIX = ".truth.json"

TEAM_BY_ID = {100: "ORDER", 200: "CHAOS"}
SMITE_SPELL_IDS = frozenset({11})
GANK_KINDS = frozenset({"jungler_approach", "roam_approach", "collapse"})

NEAR_R = 0.25               # "near me" (normalized minimap distance, ~3700 game units)
CONFIRM_BEFORE_S = 5.0      # an anchor this long before the alert ...
CONFIRM_AFTER_S = 20.0      # ... or this long after it can confirm it
ANCHOR_MAX_GAP_S = 90.0     # anchors farther than this from the alert are ignored
EPISODE_MERGE_S = 10.0      # alerts on the same target closer than this = one episode
MISSED_WINDOW_S = 15.0      # a gank alert in the 15 s before a death = "warned"
FOG_MATCH_S = 1.5           # fog sample <-> truth anchor time tolerance
FOG_MARGIN = 0.02
SIGHT_MATCH_S = 1.0         # sighting <-> truth frame time tolerance
SIGHT_OK_R = 0.08           # sighting within this of the truth = correct identification
MY_POS_TOL_S = 3.0
#: Max movement speed for the reachability bounds (normalized units / s): 425 units/s + Flash.
MAX_SPEED = 425.0 / geometry.MAP_GAME_UNITS
FLASH = 0.027
PATH_END_S = 900.0          # jungler path: first 15 minutes
SENS_MIN, SENS_MAX, SENS_STEP = 0.6, 1.6, 0.1

POSITION_FR = {"TOP": "haut", "JUNGLE": "jungle", "MIDDLE": "milieu", "BOTTOM": "tireur", "UTILITY": "support"}


# ---------------------------------------------------------------------------------- helpers
def _f(x: Any, default: float | None = None) -> float | None:
    if x is None or isinstance(x, bool):
        return default
    try:
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        return default
    return v if math.isfinite(v) else default


def _i(x: Any, default: int | None = None) -> int | None:
    v = _f(x)
    return int(v) if v is not None else default


def _norm(x: Any) -> str:
    return "".join(c for c in str(x or "").lower() if c.isalnum())


def fmt_time(gt: Any) -> str:
    v = _f(gt)
    if v is None or v < 0:
        return "—"
    s = int(round(v))
    return f"{s // 60}:{s % 60:02d}"


def _uv(pos: Any) -> tuple[float, float] | None:
    if not isinstance(pos, dict):
        return None
    x, y = _f(pos.get("x")), _f(pos.get("y"))
    if x is None or y is None:
        return None
    u, v = geometry.game_to_uv(x, y)
    return round(min(1.0, max(0.0, u)), 4), round(min(1.0, max(0.0, v)), 4)


def _dist(a: Iterable[float], b: Iterable[float]) -> float:
    (au, av), (bu, bv) = tuple(a)[:2], tuple(b)[:2]
    return math.hypot(au - bu, av - bv)


def _zone_label(u: float, v: float, team: str | None) -> str:
    try:
        return geometry.zone_name_fr(geometry.classify_zone(u, v), team) or "zone inconnue"
    except Exception:
        return "zone inconnue"


def _lane_position(lane: Any, role: Any) -> str:
    lane, role = str(lane or "").upper(), str(role or "").upper()
    if lane == "JUNGLE":
        return "JUNGLE"
    if lane == "TOP":
        return "TOP"
    if lane in ("MIDDLE", "MID"):
        return "MIDDLE"
    if lane in ("BOTTOM", "BOT"):
        return "UTILITY" if "SUPPORT" in role else "BOTTOM"
    return ""


# ---------------------------------------------------------------------------------- build
def _participants(game: dict, record: dict | None, alias_of: Callable[[int], str | None] | None) -> list[dict]:
    roster = [p for p in ((record or {}).get("roster") or []) if isinstance(p, dict)]
    by_alias_team = {(_norm(p.get("alias")), str(p.get("team") or "").upper()): p for p in roster}
    idents: dict[int, dict] = {}
    for it in game.get("participantIdentities") or []:
        if isinstance(it, dict) and isinstance(it.get("player"), dict):
            pid = _i(it.get("participantId"))
            if pid is not None:
                idents[pid] = it["player"]
    out = []
    for p in game.get("participants") or []:
        if not isinstance(p, dict):
            continue
        pid = _i(p.get("participantId"))
        if pid is None:
            continue
        cid = _i(p.get("championId"), 0) or 0
        alias = None
        if alias_of is not None and cid:
            try:
                alias = alias_of(cid)
            except Exception:
                alias = None
        team = TEAM_BY_ID.get(_i(p.get("teamId"), 0) or 0, "")
        smite = bool({_i(p.get("spell1Id")), _i(p.get("spell2Id"))} & SMITE_SPELL_IDS)
        rp = by_alias_team.get((_norm(alias), team)) if alias else None
        tl = p.get("timeline") if isinstance(p.get("timeline"), dict) else {}
        position = str((rp or {}).get("position") or "").upper() or ("JUNGLE" if smite else "") or \
            _lane_position(tl.get("lane"), tl.get("role"))
        player = idents.get(pid) or {}
        riot_id = ""
        if player.get("gameName"):
            riot_id = f"{player.get('gameName')}#{player.get('tagLine') or ''}".rstrip("#")
        out.append({"id": pid, "team": team, "champion_id": cid, "alias": alias or (rp or {}).get("alias") or "",
                    "name": (rp or {}).get("name") or alias or "", "position": position, "smite": smite,
                    "_riot_id": riot_id, "_puuid": str(player.get("puuid") or "")})
    out.sort(key=lambda d: d["id"])
    return out


def _find_me(parts: list[dict], me_pid: Any, record: dict | None) -> int | None:
    pid = _i(me_pid)
    if pid is not None and any(p["id"] == pid for p in parts):
        return pid
    meta = (record or {}).get("meta") or {}
    rid = _norm(meta.get("riot_id"))
    if rid:
        for p in parts:
            if p["_riot_id"] and _norm(p["_riot_id"]) == rid:
                return p["id"]
    champ, team = _norm(meta.get("champion")), str(meta.get("team") or "").upper()
    if champ:
        for p in parts:
            if _norm(p["alias"]) == champ and (not team or p["team"] == team):
                return p["id"]
    return None


def build_truth(game: Any, timeline: Any, *, me_participant_id: Any = None, record: dict | None = None,
                alias_of: Callable[[int], str | None] | None = None) -> dict | None:
    """Compact ground truth (see module docstring) or None when the data is unusable."""
    try:
        if not isinstance(game, dict) or not isinstance(timeline, dict):
            return None
        raw_frames = [f for f in timeline.get("frames") or [] if isinstance(f, dict)]
        if not raw_frames:
            return None
        parts = _participants(game, record, alias_of)
        me = _find_me(parts, me_participant_id, record)
        frames: list[list[Any]] = []
        kills: list[list[Any]] = []
        monsters: list[list[Any]] = []
        buildings: list[list[Any]] = []
        for fr in raw_frames:
            t = (_f(fr.get("timestamp"), 0.0) or 0.0) / 1000.0
            pf = fr.get("participantFrames")
            row: dict[str, list[Any]] = {}
            if isinstance(pf, dict):
                for k, d in pf.items():
                    if not isinstance(d, dict):
                        continue
                    pid = _i(d.get("participantId"), _i(k))
                    uv = _uv(d.get("position"))
                    if pid is None or uv is None:
                        continue
                    cs = (_i(d.get("minionsKilled"), 0) or 0) + (_i(d.get("jungleMinionsKilled"), 0) or 0)
                    row[str(pid)] = [uv[0], uv[1], _i(d.get("totalGold"), 0), _i(d.get("xp"), 0), cs,
                                     _i(d.get("level"), 1)]
            if row:
                frames.append([round(t, 1), row])
            for ev in fr.get("events") or []:
                if not isinstance(ev, dict):
                    continue
                et = round((_f(ev.get("timestamp"), 0.0) or 0.0) / 1000.0, 1)
                typ = str(ev.get("type") or "")
                uv = _uv(ev.get("position"))
                if typ == "CHAMPION_KILL" and uv is not None:
                    assists = [a for a in (_i(x) for x in ev.get("assistingParticipantIds") or []) if a]
                    kills.append([et, _i(ev.get("killerId"), 0), _i(ev.get("victimId"), 0), assists, uv[0], uv[1]])
                elif typ == "ELITE_MONSTER_KILL":
                    team = TEAM_BY_ID.get(_i(ev.get("killerTeamId"), 0) or 0, "")
                    killer = _i(ev.get("killerId"), 0) or 0
                    if not team and killer:
                        team = next((p["team"] for p in parts if p["id"] == killer), "")
                    monsters.append([et, killer, team, str(ev.get("monsterType") or ""),
                                     str(ev.get("monsterSubType") or ""), *(uv or (None, None))])
                elif typ == "BUILDING_KILL":
                    buildings.append([et, _i(ev.get("killerId"), 0) or 0,
                                      TEAM_BY_ID.get(_i(ev.get("teamId"), 0) or 0, ""),
                                      str(ev.get("buildingType") or ""), str(ev.get("laneType") or ""),
                                      str(ev.get("towerType") or ""), *(uv or (None, None))])
        if not frames:
            return None
        my_team = next((p["team"] for p in parts if p["id"] == me), "") if me else ""
        my_team = my_team or str(((record or {}).get("meta") or {}).get("team") or "").upper()
        enemies = [p for p in parts if my_team and p["team"] and p["team"] != my_team]
        allies = [p for p in parts if my_team and p["team"] == my_team]
        last_row = frames[-1][1]

        def jungler_of(side: list[dict]) -> int | None:
            cands = [p for p in side if p["position"] == "JUNGLE"] or [p for p in side if p["smite"]]
            if not cands:
                return None
            # most jungle camps (CS) at the end when several candidates
            return max(cands, key=lambda p: (p["smite"], (last_row.get(str(p["id"])) or [0, 0, 0, 0, 0])[4]))["id"]

        me_part = next((p for p in parts if p["id"] == me), None)
        opp = None
        if me_part is not None and me_part["position"]:
            if me_part["position"] == "JUNGLE":
                opp = jungler_of(enemies)
            else:
                opp = next((p["id"] for p in enemies if p["position"] == me_part["position"]), None)
        dur = _f(game.get("gameDuration"))
        if dur is not None and dur > 100000:
            dur /= 1000.0
        rec_path = (record or {}).get("_path")
        truth = {
            "schema": SCHEMA_VERSION, "source": "lcu",
            "game_id": _i(game.get("gameId")), "game_creation": _i(game.get("gameCreation")),
            "duration": dur if dur is not None else frames[-1][0], "queue_id": _i(game.get("queueId")),
            "game_mode": str(game.get("gameMode") or ""), "game_version": str(game.get("gameVersion") or ""),
            "record": Path(rec_path).name if rec_path else None,
            "me": me, "my_team": my_team, "enemy_jungler": jungler_of(enemies), "my_jungler": jungler_of(allies),
            "lane_opponent": opp,
            "participants": [{k: v for k, v in p.items() if not k.startswith("_")} for p in parts],
            "frame_interval": round((frames[1][0] - frames[0][0]) if len(frames) > 1 else 60.0, 1),
            "frames": frames, "kills": kills, "monsters": monsters, "buildings": buildings,
        }
        return truth
    except Exception:
        log.exception("build_truth failed")
        return None


# ---------------------------------------------------------------------------------- normalized view
class _Truth:
    """Indexed view of a truth dict."""

    def __init__(self, truth: dict) -> None:
        self.raw = truth
        self.parts = {(_i(p.get("id")) or 0): p for p in truth.get("participants") or [] if isinstance(p, dict)}
        self.me = _i(truth.get("me"))
        self.my_team = str(truth.get("my_team") or "")
        self.jungler = _i(truth.get("enemy_jungler"))
        self.opp = _i(truth.get("lane_opponent"))
        self.frames: list[tuple[float, dict]] = []
        for fr in truth.get("frames") or []:
            try:
                t = _f(fr[0])
                row = fr[1]
            except (TypeError, IndexError, KeyError):
                continue
            if t is not None and isinstance(row, dict):
                self.frames.append((t, row))
        self.frames.sort(key=lambda x: x[0])
        self.kills = []
        for k in truth.get("kills") or []:
            try:
                t, killer, victim, assists, u, v = k[:6]
            except (TypeError, ValueError):
                continue
            if _f(t) is None or _f(u) is None or _f(v) is None:
                continue
            self.kills.append((float(t), _i(killer, 0), _i(victim, 0),
                               [a for a in (_i(x) for x in (assists or [])) if a], (float(u), float(v))))
        self.kills.sort(key=lambda k: k[0])
        self.monsters = [m for m in truth.get("monsters") or [] if isinstance(m, list) and len(m) >= 7]

    def alias(self, pid: Any) -> str:
        return str((self.parts.get(_i(pid) or -1) or {}).get("alias") or "")

    def name(self, pid: Any) -> str:
        p = self.parts.get(_i(pid) or -1) or {}
        return str(p.get("name") or p.get("alias") or "?")

    def pid_of_alias(self, alias: Any, enemy_only: bool = True) -> int | None:
        a = _norm(alias)
        if not a:
            return None
        for pid, p in self.parts.items():
            if _norm(p.get("alias")) == a and (not enemy_only or p.get("team") != self.my_team):
                return pid
        return None

    def frame_series(self, pid: int | None) -> list[tuple[float, float, float]]:
        if pid is None:
            return []
        out = []
        for t, row in self.frames:
            d = row.get(str(pid))
            if isinstance(d, list) and len(d) >= 2 and _f(d[0]) is not None and _f(d[1]) is not None:
                out.append((t, float(d[0]), float(d[1])))
        return out

    def frame_at(self, minute: float, pid: int | None) -> list | None:
        if pid is None:
            return None
        best = None
        for t, row in self.frames:
            if abs(t - minute * 60.0) <= 30.0 and str(pid) in row:
                if best is None or abs(t - minute * 60.0) < abs(best[0] - minute * 60.0):
                    best = (t, row[str(pid)])
        return best[1] if best else None

    def anchors(self, pid: int | None) -> list[tuple[float, tuple[float, float], float, bool]]:
        """True-position samples of ``pid``: (t, uv, slack, exact) from frames, kills, monsters."""
        if pid is None:
            return []
        out = [(t, (u, v), 0.0, True) for t, u, v in self.frame_series(pid)]
        for t, killer, victim, assists, uv in self.kills:
            if victim == pid:
                out.append((t, uv, 0.0, True))
            elif killer == pid or pid in assists:
                out.append((t, uv, 0.06, False))
        for m in self.monsters:
            if _i(m[1]) == pid and _f(m[5]) is not None and _f(m[6]) is not None:
                out.append((float(m[0]), (float(m[5]), float(m[6])), 0.05, False))
        out.sort(key=lambda a: a[0])
        return out


def _series(raw: Any) -> list[tuple[float, float, float]]:
    out = []
    for p in raw or []:
        try:
            t, u, v = _f(p[0]), _f(p[1]), _f(p[2])
        except (TypeError, IndexError, KeyError):
            continue
        if t is not None and u is not None and v is not None:
            out.append((t, u, v))
    out.sort(key=lambda x: x[0])
    return out


def _nearest(series: list[tuple[float, float, float]], times: list[float], t: float,
             tol: float) -> tuple[float, float, float] | None:
    if not series:
        return None
    i = bisect.bisect_left(times, t)
    best = None
    for k in (i - 1, i):
        if 0 <= k < len(series) and abs(series[k][0] - t) <= tol:
            if best is None or abs(series[k][0] - t) < abs(best[0] - t):
                best = series[k]
    return best


# ---------------------------------------------------------------------------------- analysis
def _my_pos_fn(record: dict, tr: _Truth) -> Callable[[float], tuple[float, float] | None]:
    mine = _series(record.get("my_positions"))
    times = [p[0] for p in mine]
    my_frames = tr.frame_series(tr.me)
    ftimes = [p[0] for p in my_frames]
    my_deaths = [(t, uv) for t, _k, victim, _a, uv in tr.kills if victim == tr.me]

    def at(t: float) -> tuple[float, float] | None:
        p = _nearest(mine, times, t, MY_POS_TOL_S)
        if p is not None:
            return p[1], p[2]
        for dt, uv in my_deaths:
            if abs(dt - t) <= MY_POS_TOL_S:
                return uv
        p = _nearest(my_frames, ftimes, t, 5.0)
        return (p[1], p[2]) if p is not None else None
    return at


def _alerts(record: dict) -> list[tuple[float, str, int, str]]:
    out = []
    for a in record.get("alerts") or []:
        if not isinstance(a, list) or len(a) < 3:
            continue
        t = _f(a[0])
        kind = str(a[1] or "").lower()
        if t is None or kind not in GANK_KINDS:
            continue
        alias = str(a[4]) if len(a) >= 5 and a[4] else ""
        out.append((t, kind, _i(a[2], 0) or 0, alias))
    out.sort(key=lambda x: x[0])
    return out


def _classify(T: float, m: tuple[float, float] | None, anchors: list, my_pos: Callable) -> str:
    if m is None or not anchors:
        return "unknown"
    for t, uv, slack, _exact in anchors:
        if T - CONFIRM_BEFORE_S <= t <= T + CONFIRM_AFTER_S:
            mm = my_pos(t) if abs(t - T) > 2.0 else m
            mm = mm or (m if abs(t - T) <= CONFIRM_BEFORE_S else None)
            if mm is not None and _dist(uv, mm) <= NEAR_R + slack:
                return "confirmed"
    before = [a for a in anchors if T - ANCHOR_MAX_GAP_S <= a[0] <= T]
    after = [a for a in anchors if T <= a[0] <= T + ANCHOR_MAX_GAP_S]
    a0 = before[-1] if before else None
    a1 = after[0] if after else None
    for a in (a0, a1):
        if a is not None and _dist(a[1], m) - (MAX_SPEED * abs(a[0] - T) + FLASH) - a[2] > NEAR_R:
            return "false"
    if a0 is not None and a1 is not None and a1[0] - a0[0] <= 120.0:
        span = a1[0] - a0[0]
        k = 0.5 if span <= 0 else (T - a0[0]) / span
        p = (a0[1][0] + k * (a1[1][0] - a0[1][0]), a0[1][1] + k * (a1[1][1] - a0[1][1]))
        if _dist(p, m) > 2.0 * NEAR_R:
            return "probable_false"
    return "unknown"


def _reliability(record: dict, tr: _Truth, my_pos: Callable) -> dict[str, Any]:
    alerts = _alerts(record)
    # 1) gank alert precision (episodes per target)
    episodes: list[dict] = []
    last_by_target: dict[int, float] = {}
    for t, kind, level, alias in alerts:
        target = tr.pid_of_alias(alias) if alias else None
        if target is None and kind == "jungler_approach":
            target = tr.jungler
        if target is None:
            continue
        if t - last_by_target.get(target, -1e9) < EPISODE_MERGE_S:
            last_by_target[target] = t
            continue
        last_by_target[target] = t
        verdict = _classify(t, my_pos(t), tr.anchors(target), my_pos)
        episodes.append({"game_time": round(t, 1), "time": fmt_time(t), "kind": kind, "level": level,
                         "target": tr.alias(target), "target_name": tr.name(target), "verdict": verdict})
    n_conf = sum(1 for e in episodes if e["verdict"] == "confirmed")
    n_false = sum(1 for e in episodes if e["verdict"] == "false")
    n_prob = sum(1 for e in episodes if e["verdict"] == "probable_false")
    scored = n_conf + n_false + n_prob
    precision = (n_conf / scored) if scored else None
    # 2) missed ganks: my deaths with the enemy jungler involved and no gank alert just before
    jdeaths = []
    for t, killer, victim, assists, uv in tr.kills:
        if victim != tr.me or tr.me is None or tr.jungler is None:
            continue
        if killer == tr.jungler or tr.jungler in assists:
            warned = any(t - MISSED_WINDOW_S <= a[0] <= t + 1.0 for a in alerts)
            jdeaths.append({"game_time": round(t, 1), "time": fmt_time(t), "warned": warned, "uv": list(uv)})
    missed = [d for d in jdeaths if not d["warned"]]
    # 3) fog circle coverage of the true jungler position
    jalias = _norm(tr.alias(tr.jungler))
    fog = []
    for f in record.get("fog") or []:
        if isinstance(f, list) and len(f) >= 5 and _norm(f[1]) == jalias and jalias:
            t, u, v, r = _f(f[0]), _f(f[2]), _f(f[3]), _f(f[4])
            if None not in (t, u, v, r):
                fog.append((t, u, v, r))
    fog.sort(key=lambda x: x[0])
    ftimes = [x[0] for x in fog]
    fog_checks = fog_inside = 0
    for t, uv, slack, exact in tr.anchors(tr.jungler):
        if not exact or not fog:
            continue
        i = bisect.bisect_left(ftimes, t)
        best = None
        for k in (i - 1, i):
            if 0 <= k < len(fog) and abs(fog[k][0] - t) <= FOG_MATCH_S:
                if best is None or abs(fog[k][0] - t) < abs(best[0] - t):
                    best = fog[k]
        if best is None:
            continue
        fog_checks += 1
        if _dist(uv, (best[1], best[2])) <= best[3] + FOG_MARGIN:
            fog_inside += 1
    coverage = (fog_inside / fog_checks) if fog_checks else None
    # 4) identification accuracy of the minimap sightings (exact truth frames only)
    sight_n = sight_ok = 0
    errors: list[float] = []
    sightings = record.get("sightings") if isinstance(record.get("sightings"), dict) else {}
    for key, pts in sightings.items():
        pid = tr.pid_of_alias(key)
        if pid is None:
            continue
        ser = _series(pts)
        times = [p[0] for p in ser]
        for t, u, v in tr.frame_series(pid):
            s = _nearest(ser, times, t, SIGHT_MATCH_S)
            if s is None:
                continue
            e = _dist((s[1], s[2]), (u, v))
            sight_n += 1
            errors.append(e)
            if e <= SIGHT_OK_R:
                sight_ok += 1
    errors.sort()
    out = {
        "alerts": len(episodes), "confirmed": n_conf, "false": n_false, "probable_false": n_prob,
        "unknown": len(episodes) - scored, "precision": None if precision is None else round(precision, 3),
        "episodes": episodes[:60],
        "jungler_deaths": len(jdeaths), "missed": len(missed), "missed_times": [d["time"] for d in missed],
        "fog_checks": fog_checks, "fog_inside": fog_inside,
        "fog_coverage": None if coverage is None else round(coverage, 3),
        "sightings_checked": sight_n, "sightings_ok": sight_ok,
        "sighting_error_median": round(errors[len(errors) // 2], 4) if errors else None,
    }
    sens = _f(((record.get("settings") or {}) if isinstance(record.get("settings"), dict) else {}).get("sensitivity"))
    out["suggestion"] = suggest_sensitivity(out, sens)
    out["grade"], out["grade_label"] = _grade(out)
    return out


def _grade(rel: dict) -> tuple[int | None, str]:
    parts = []
    if rel.get("precision") is not None:
        parts.append(float(rel["precision"]))
    if rel.get("jungler_deaths"):
        parts.append(1.0 - rel["missed"] / max(1, rel["jungler_deaths"]))
    if rel.get("fog_coverage") is not None:
        parts.append(float(rel["fog_coverage"]))
    if rel.get("sightings_checked"):
        parts.append(rel["sightings_ok"] / max(1, rel["sightings_checked"]))
    if not parts:
        return None, "pas assez de données"
    g = int(round(100 * sum(parts) / len(parts)))
    return g, "bonne" if g >= 75 else "moyenne" if g >= 50 else "faible"


def suggest_sensitivity(rel: dict, current: float | None = None, games: int = 1) -> dict[str, Any]:
    """Sensitivity advice from a reliability dict (one game or aggregated)."""
    cur = current if current is not None else 1.0
    precision = rel.get("precision")
    scored = (rel.get("confirmed") or 0) + (rel.get("false") or 0) + (rel.get("probable_false") or 0)
    missed = rel.get("missed") or 0
    jd = rel.get("jungler_deaths") or 0
    delta = 0.0
    if missed >= 2 or (jd and missed / jd >= 0.5 and missed >= 1 and games >= 3):
        if precision is not None and precision < 0.35 and scored >= 3:
            text = ("Des ganks n'ont pas été annoncés, mais les alertes données étaient souvent fausses : "
                    "le problème vient de la détection (minimap), pas de la sensibilité.")
        else:
            delta = SENS_STEP
            text = (f"{missed} gank{'s' if missed > 1 else ''} du jungler sans alerte : augmente la sensibilité "
                    "pour être prévenu plus tôt.")
    elif scored >= 3 and precision is not None and precision < 0.5 and missed == 0:
        delta = -SENS_STEP
        text = ("Beaucoup d'alertes ne correspondaient pas au vrai jungler : baisse un peu la sensibilité "
                "pour moins de fausses alertes.")
    elif scored == 0 and not jd:
        text = "Pas assez d'alertes ou de ganks pour juger : garde le réglage actuel."
    else:
        text = "Les alertes collaient à la réalité : garde la sensibilité actuelle."
    sug = round(min(SENS_MAX, max(SENS_MIN, cur + delta)), 2)
    if sug == round(cur, 2):
        delta = 0.0
    return {"delta": round(delta, 2), "current": round(cur, 2) if current is not None else None,
            "suggested": sug if delta else None, "text": text}


def _jungler_path(tr: _Truth) -> dict[str, Any]:
    out: dict[str, Any] = {"alias": tr.alias(tr.jungler), "name": tr.name(tr.jungler) if tr.jungler else "",
                           "path": [], "first_side": None, "first_side_label": None, "first_time": None,
                           "kills_involved": 0}
    if tr.jungler is None:
        return out
    jteam = (tr.parts.get(tr.jungler) or {}).get("team")
    for t, u, v in tr.frame_series(tr.jungler):
        if t < 50.0 or t > PATH_END_S + 1.0:
            continue
        z = geometry.classify_zone(u, v)
        out["path"].append({"game_time": round(t, 1), "time": fmt_time(t), "uv": [round(u, 4), round(v, 4)],
                            "zone_label": _zone_label(u, v, tr.my_team), "side": geometry.side_of(u, v),
                            "in_base": bool(geometry.is_base(z) and geometry.zone_owner(z) == jteam)})
    for p in out["path"]:
        if not p["in_base"]:
            out["first_side"] = p["side"]
            out["first_side_label"] = "côté haut" if p["side"] == "top" else "côté bas"
            out["first_time"] = p["time"]
            break
    out["kills_involved"] = sum(1 for _t, k, _v, a, _uv in tr.kills
                                if k == tr.jungler or tr.jungler in a)
    early = [(t, k, v, a) for t, k, v, a, _uv in tr.kills if t <= PATH_END_S and (k == tr.jungler or tr.jungler in a)]
    out["early_ganks"] = [{"time": fmt_time(t), "victim": tr.name(v)} for t, k, v, a in early][:10]
    return out


def _lane(tr: _Truth) -> dict[str, Any]:
    out: dict[str, Any] = {"opponent": tr.alias(tr.opp) if tr.opp else None,
                           "opponent_name": tr.name(tr.opp) if tr.opp else None, "rows": []}
    for minute in (10, 15):
        me = tr.frame_at(minute, tr.me)
        if me is None:
            continue
        row: dict[str, Any] = {"minute": minute, "gold": me[2], "xp": me[3], "cs": me[4], "level": me[5]}
        opp = tr.frame_at(minute, tr.opp)
        if opp is not None:
            row.update({"opp_gold": opp[2], "opp_cs": opp[4], "gold_diff": (me[2] or 0) - (opp[2] or 0),
                        "xp_diff": (me[3] or 0) - (opp[3] or 0), "cs_diff": (me[4] or 0) - (opp[4] or 0),
                        "level_diff": (me[5] or 0) - (opp[5] or 0)})
        out["rows"].append(row)
    r10 = next((r for r in out["rows"] if r["minute"] == 10), None)
    out["cs10"] = r10["cs"] if r10 else None
    out["gold_diff10"] = r10.get("gold_diff") if r10 else None
    r15 = next((r for r in out["rows"] if r["minute"] == 15), None)
    out["gold_diff15"] = r15.get("gold_diff") if r15 else None
    return out


def _deaths(record: dict, tr: _Truth) -> list[dict]:
    alerts = _alerts(record)
    out = []
    for t, killer, victim, assists, uv in tr.kills:
        if tr.me is None or victim != tr.me:
            continue
        before = [a for a in alerts if t - MISSED_WINDOW_S <= a[0] <= t + 1.0]
        involved = killer == tr.jungler or (tr.jungler in assists if tr.jungler else False)
        out.append({"game_time": round(t, 1), "time": fmt_time(t), "uv": [round(uv[0], 4), round(uv[1], 4)],
                    "zone_label": _zone_label(uv[0], uv[1], tr.my_team),
                    "killer": tr.name(killer) if killer else "exécution", "killer_alias": tr.alias(killer),
                    "assisters": [tr.name(a) for a in assists], "jungler_involved": bool(involved),
                    "warned": bool(before), "alert_before_s": round(t - before[-1][0], 1) if before else None})
    return out


def _objectives(tr: _Truth) -> list[dict]:
    label = {"DRAGON": "Dragon", "BARON_NASHOR": "Baron", "RIFTHERALD": "Héraut", "HORDE": "Larve",
             "ATAKHAN": "Atakhan", "ELDER_DRAGON": "Dragon ancestral"}
    out = []
    for m in tr.monsters:
        t = _f(m[0])
        if t is None:
            continue
        team = str(m[2] or "")
        out.append({"time": fmt_time(t), "label": label.get(str(m[3]), str(m[3]).title() or "?"),
                    "ours": bool(team and team == tr.my_team), "killer": tr.name(m[1]) if m[1] else ""})
    return out[:40]


def analyze_truth(record: Any, truth: Any) -> dict[str, Any]:
    """Compare the record with the truth (see module docstring). Never raises."""
    try:
        if not isinstance(truth, dict) or not truth.get("frames"):
            return {"available": False}
        rec = record if isinstance(record, dict) else {}
        tr = _Truth(truth)
        if tr.me is None:
            return {"available": False, "reason": "joueur introuvable dans la partie du client"}
        my_pos = _my_pos_fn(rec, tr)
        me = tr.parts.get(tr.me) or {}
        out: dict[str, Any] = {"available": True, "game_id": truth.get("game_id"),
                               "me": {"alias": me.get("alias"), "name": me.get("name"), "team": tr.my_team,
                                      "position": me.get("position")}}
        for name, fn, default in (("deaths", lambda: _deaths(rec, tr), []),
                                  ("jungler", lambda: _jungler_path(tr), {"path": []}),
                                  ("lane", lambda: _lane(tr), {"rows": []}),
                                  ("objectives", lambda: _objectives(tr), []),
                                  ("reliability", lambda: _reliability(rec, tr, my_pos), {})):
            try:
                out[name] = fn()
            except Exception:
                log.exception("analyze_truth: section %s failed", name)
                out[name] = default
        return out
    except Exception:
        log.exception("analyze_truth failed")
        return {"available": False}


# ---------------------------------------------------------------------------------- score / files
SCORE_KEYS = ("alerts", "confirmed", "false", "probable_false", "unknown", "jungler_deaths", "missed",
              "fog_checks", "fog_inside", "sightings_checked", "sightings_ok")


def compact_score(reliability: Any, record: Any = None) -> dict[str, Any]:
    """The numbers of a reliability dict worth keeping for calibration."""
    rel = reliability if isinstance(reliability, dict) else {}
    out = {k: int(rel.get(k) or 0) for k in SCORE_KEYS}
    for k in ("precision", "fog_coverage", "sighting_error_median", "grade"):
        out[k] = rel.get(k)
    settings = (record or {}).get("settings") if isinstance(record, dict) else None
    if isinstance(settings, dict):
        out["settings"] = {k: settings[k] for k in list(settings)[:12]
                           if isinstance(settings[k], (int, float, str, bool))}
    return out


def truth_path_for(record_path: Path | str) -> Path:
    """``games/X.json`` -> ``games/truth/X.truth.json``."""
    p = Path(record_path)
    name = p.name
    for suf in (".partial.json", ".json"):
        if name.endswith(suf):
            name = name[: -len(suf)]
            break
    return p.parent / TRUTH_DIR_NAME / (name + TRUTH_SUFFIX)


def save_truth(record_path: Path | str, truth: dict) -> Path | None:
    """Write the truth file next to the record (atomic). Never raises."""
    try:
        from treeaicoach.recorder import atomic_write_text

        out = truth_path_for(record_path)
        text = json.dumps(truth, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        return out if atomic_write_text(out, text) else None
    except Exception:
        log.exception("save_truth failed")
        return None


def load_truth(record_path: Path | str) -> dict | None:
    """The truth of a record, if it was fetched. Never raises."""
    try:
        p = truth_path_for(record_path)
        if not p.is_file():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("frames") else None
    except Exception as exc:
        log.debug("Cannot read the truth of %s: %s", record_path, exc)
        return None


def aggregate_scores(truth_dir: Path | str, last: int = 10) -> dict[str, Any]:
    """Sum of the ``score`` of the last ``last`` truth files (newest by game creation)."""
    out: dict[str, Any] = {"games": 0}
    try:
        d = Path(truth_dir)
        if not d.is_dir():
            return out
        rows = []
        for p in d.iterdir():
            if not p.name.endswith(TRUTH_SUFFIX):
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict) and isinstance(data.get("score"), dict):
                rows.append((_i(data.get("game_creation"), 0) or 0, p.name, data["score"]))
        rows.sort(reverse=True)
        rows = rows[: max(1, int(last))]
        tot = {k: sum(int(_f(r[2].get(k), 0) or 0) for r in rows) for k in SCORE_KEYS}
        scored = tot["confirmed"] + tot["false"] + tot["probable_false"]
        out.update(tot)
        out["games"] = len(rows)
        out["precision"] = round(tot["confirmed"] / scored, 3) if scored else None
        out["fog_coverage"] = round(tot["fog_inside"] / tot["fog_checks"], 3) if tot["fog_checks"] else None
        sens = None
        for r in rows:
            s = r[2].get("settings")
            if isinstance(s, dict) and _f(s.get("sensitivity")) is not None:
                sens = _f(s.get("sensitivity"))
                break
        out["suggestion"] = suggest_sensitivity(out, sens, games=len(rows))
    except Exception:
        log.exception("aggregate_scores failed")
    return out
