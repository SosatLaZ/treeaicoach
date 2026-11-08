"""Which quests a route may contain, and how long a route may be (agent QUESTQA).

The SAME rules run in the addon (``Modules/ZonePlan.lua``: ``Z.Kind`` / ``Z:Accept``, ``Route.lua`` horizon) and
here, so a plan written by the app never shows the player something the in-game route would refuse:

* a quest ready to turn in is always kept (the turn-in is a normal NPC), unless the player ignored it;
* PvP and raid quests never;
* dungeon quests (quest tag, quest zone = an instance, an objective that is a boss) only when the player
  opted in AND the party has at least 3 members;
* elite quests only when the player opted in, or with 3 or more;
* group quests (suggested group ≥ 2) only when the player allows them, or in a group;
* not more than ``gap`` levels above the player (default 3), ready-to-turn-in quests excepted;
* a failed quest (timer ran out, escort died) can no longer progress: never planned (abandon / take again).

Length: a route covers at most ``HORIZON_MIN`` minutes (60–120, the session when known) and ``MAX_NODES`` task
stops; what is beyond is planned again later, from the then-current quest log.
"""

from __future__ import annotations

from typing import Any

GAP_DEFAULT = 3
HORIZON_MIN = 90.0          # minutes covered by one plan (clamped 60–120, the session length when known)
HORIZON_LO, HORIZON_HI = 60.0, 120.0
MAX_NODES = 45              # pick / objective / turn-in stops in one plan (the in-game route keeps ≤ 40 stops)

#: quest tag ids (GetQuestTagInfo): 1 elite / group, 41 PvP, 62 raid, 81 dungeon, 85 heroic, 88/89 raid 10/25
TAG_ELITE, TAG_PVP, TAG_RAID, TAG_DUNGEON = 1, 41, 62, 81
RAID_TAGS = {62, 88, 89}
DUNGEON_TAGS = {81, 85}


def _n(v: Any, d: float = 0.0) -> float:
    try:
        return d if v is None else float(v)
    except (TypeError, ValueError):
        return d


def _objs(raw: dict) -> list[dict]:
    o = raw.get("o")
    if isinstance(o, dict):
        try:
            o = [o[k] for k in sorted(o, key=lambda k: float(k))]
        except (TypeError, ValueError):
            o = list(o.values())
    return [x for x in (o or []) if isinstance(x, dict)]


def kind(raw: dict) -> dict[str, bool]:
    """Flags of one export quest record: elite, group, dungeon, raid, pvp, boss, failed, ready."""
    tag = int(_n(raw.get("tag")))
    ranks = [int(_n(o.get("rank"))) for o in _objs(raw)]
    boss = any(r == 3 for r in ranks)
    return {
        "elite": bool(int(_n(raw.get("elite")))) or tag == TAG_ELITE or any(r in (1, 2) for r in ranks),
        "group": int(_n(raw.get("grp"))) > 1,
        "dungeon": bool(int(_n(raw.get("dg")))) or tag in DUNGEON_TAGS or boss,
        "raid": bool(int(_n(raw.get("raid")))) or tag in RAID_TAGS,
        "pvp": bool(int(_n(raw.get("pvp")))) or tag == TAG_PVP,
        "boss": boss,
        "failed": bool(int(_n(raw.get("failed")))),
        "ready": bool(int(_n(raw.get("done")))),
    }


def options(exp: dict | None) -> dict[str, Any]:
    """The player's route options (``export.work.opts``, Modules/ZonePlan.lua defaults when absent)."""
    w = (exp or {}).get("work") if isinstance((exp or {}).get("work"), dict) else {}
    o = w.get("opts") if isinstance(w.get("opts"), dict) else {}

    def flag(k: str, d: bool) -> bool:
        v = o.get(k)
        return d if v is None else bool(v) and v != 0

    gap = int(_n(o.get("gap"), GAP_DEFAULT)) or GAP_DEFAULT
    return {"elites": flag("elites", False), "groupQ": flag("groupQ", True), "dungeons": flag("dungeons", False),
            "gap": max(1, min(10, gap))}


def accept(raw: dict, level: int, group: int = 1, opts: dict | None = None,
           ignored: set[int] | None = None) -> tuple[bool, str]:
    """(keep?, why) — mirror of ZonePlan:Accept (why: ignorée, échouée, JcJ, raid, donjon, élite, groupe, niveau)."""
    opts = opts or options(None)
    qid = int(_n(raw.get("id")))
    if ignored and qid in ignored:
        return False, "ignorée"
    k = kind(raw)
    if k["failed"]:
        return False, "échouée"
    if k["ready"] and raw.get("st") == "log":
        return True, ""
    if k["pvp"]:
        return False, "JcJ"
    if k["raid"]:
        return False, "raid"
    if k["dungeon"] and not (opts.get("dungeons") and group >= 3):
        return False, "donjon"
    if k["elite"] and not (opts.get("elites") or group >= 3):
        return False, "élite"
    if k["group"] and not (opts.get("groupQ", True) or group >= 2):
        return False, "groupe"
    lv = int(_n(raw.get("lv"), level)) or level
    if lv > 0 and lv - level > int(opts.get("gap") or GAP_DEFAULT):
        return False, "niveau"
    return True, ""


def horizon_minutes(session_min: float | None) -> float:
    """Minutes one plan covers: the session when known, clamped to 60–120, else 90."""
    s = _n(session_min)
    if s <= 0:
        return HORIZON_MIN
    return max(HORIZON_LO, min(HORIZON_HI, s))


def plan_fingerprint(exp: dict | None) -> dict[str, Any]:
    """What the addon compares to decide whether a plan still matches the game (Planner.PlanFresh):
    the quests in the log when the export was written, the level, the zone and the export time."""
    exp = exp or {}
    pl = exp.get("player") if isinstance(exp.get("player"), dict) else {}
    qs = exp.get("quests")
    if isinstance(qs, dict):
        qs = list(qs.values())
    log = sorted({int(_n(q.get("id"))) for q in (qs or []) if isinstance(q, dict) and q.get("st") == "log"
                  and _n(q.get("id")) > 0})
    return {"log": log, "level": int(_n(pl.get("level"))), "exp": int(_n(exp.get("made")))}
