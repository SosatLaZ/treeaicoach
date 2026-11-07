"""Leveling / bags / group summary read from the addon's SavedVariables, for TreeAI's WoW page.

The addon (modules Leveling, Inventory, Group) writes, at /reload or logout::

    TreeAIDB.chars["Nom-Royaume"].summary = {
        class, level, zone, money, ... (core),
        leveling  = {level, xp, xpMax, rested, zone, mapID, trainPending, talentPoints, hint, zoneWarn,
                     skills = {{name, rank, max}}, quests = {{id, title, level, done, obj = {text}}}, ...},
        inventory = {free, total, greyCount, greyValue, durMin, broken, repairCost, money, sessionLoot,
                     mats = {[name] = n}},
        group     = {members = {[name] = {level, class, zone, far}},
                     shared = {{id, title, need = {"Nom : 3/8"}, done = {names}, missing}}},
    }

Pure functions over the parsed table; never raise (``log.debug`` on errors). French output strings.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

__all__ = ["format_money", "char_keys", "summarize", "lines", "load_summary"]


def _d(v: Any) -> dict:
    """Lua table -> dict (lists, i.e. tables with keys 1..n, become {1: .., 2: ..})."""
    if isinstance(v, dict):
        return v
    if isinstance(v, list):
        return {i + 1: x for i, x in enumerate(v)}
    return {}


def _l(v: Any) -> list:
    """Lua array -> list (a dict with int keys is sorted by key; empty / other -> [])."""
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        try:
            return [v[k] for k in sorted(k for k in v if isinstance(k, int))]
        except Exception:
            return []
    return []


def _num(v: Any, default: float | None = None) -> float | None:
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def format_money(copper: Any) -> str:
    """12345 -> '1 po 23 pa 45 pc'."""
    c = int(_num(copper, 0) or 0)
    if c < 0:
        c = 0
    g, s, k = c // 10000, (c // 100) % 100, c % 100
    parts = [f"{g} po"] if g else []
    if s:
        parts.append(f"{s} pa")
    if k or not parts:
        parts.append(f"{k} pc")
    return " ".join(parts)


def _summary_of(char: Any) -> dict:
    return _d(_d(char).get("summary"))


def _updated(char: Any) -> float:
    s = _summary_of(char)
    best = _num(s.get("updated"), 0) or 0
    for sec in ("leveling", "inventory", "group"):
        best = max(best, _num(_d(s.get(sec)).get("updated"), 0) or 0)
    return best


def char_keys(db: Any) -> list[str]:
    """Characters in the DB, most recently updated first."""
    try:
        chars = _d(_d(db).get("chars"))
        return sorted((str(k) for k in chars), key=lambda k: -_updated(chars.get(k)))
    except Exception:
        log.debug("leveling: char_keys failed", exc_info=True)
        return []


def summarize(db: Any, char: str | None = None) -> dict | None:
    """Flat summary of one character (the most recent one by default), or None."""
    try:
        chars = _d(_d(db).get("chars"))
        keys = char_keys(db)
        if not keys:
            return None
        key = char if char in chars else keys[0]
        c = _d(chars.get(key))
        s = _summary_of(c)
        lv, inv, grp = _d(s.get("leveling")), _d(s.get("inventory")), _d(s.get("group"))

        level = int(_num(lv.get("level"), _num(s.get("level"), 0)) or 0)
        xp, xp_max = _num(lv.get("xp"), _num(s.get("xp"))), _num(lv.get("xpMax"), _num(s.get("xpMax")))
        rested = _num(lv.get("rested"), _num(s.get("rested"), 0)) or 0
        xp_pct = round(100 * xp / xp_max) if xp is not None and xp_max else None
        rested_pct = round(100 * rested / xp_max) if xp_max else None

        skills = []
        for sk in _l(lv.get("skills")):
            sk = _d(sk)
            rank, mx = _num(sk.get("rank")), _num(sk.get("max"))
            if sk.get("name") and rank is not None and mx:
                skills.append({"name": str(sk["name"]), "rank": int(rank), "max": int(mx)})
        behind = [x for x in skills if level and x["max"] == level * 5 and x["max"] - x["rank"] >= 20]

        quests = []
        for q in _l(lv.get("quests")):
            q = _d(q)
            if not q.get("title"):
                continue
            quests.append({"id": q.get("id"), "title": str(q["title"]), "level": _num(q.get("level")),
                           "done": bool(q.get("done")), "obj": [str(o) for o in _l(q.get("obj")) if o]})

        mats = {str(k): int(_num(v, 0) or 0) for k, v in _d(inv.get("mats")).items()}
        members = {}
        for name, m in _d(grp.get("members")).items():
            m = _d(m)
            members[str(name)] = {"level": _num(m.get("level")), "class": m.get("class"),
                                  "zone": m.get("zone"), "far": _num(m.get("far"))}
        shared = []
        for e in _l(grp.get("shared")):
            e = _d(e)
            shared.append({"title": str(e.get("title") or "?"), "need": [str(x) for x in _l(e.get("need"))],
                           "done": [str(x) for x in _l(e.get("done"))], "missing": bool(e.get("missing"))})

        free = _num(inv.get("free"), _num(s.get("bagsFree")))
        total = _num(inv.get("total"), _num(s.get("bagsTotal")))
        return {
            "char": key,
            "class": lv.get("class") or s.get("class"),
            "level": level,
            "xp_pct": xp_pct,
            "rested_pct": rested_pct,
            "zone": lv.get("zone") or s.get("zone"),
            "subzone": lv.get("subzone") or s.get("subzone"),
            "money": _num(lv.get("money"), _num(inv.get("money"), _num(s.get("money")))),
            "train_pending": _num(lv.get("trainPending")),
            "talent_points": int(_num(lv.get("talentPoints"), 0) or 0),
            "zone_warn": lv.get("zoneWarn") or None,
            "hint": lv.get("hint") or None,
            "skills": skills,
            "skills_behind": behind,
            "quests": quests,
            "bags_free": None if free is None else int(free),
            "bags_total": None if total is None else int(total),
            "grey_count": int(_num(inv.get("greyCount"), 0) or 0),
            "grey_value": int(_num(inv.get("greyValue"), 0) or 0),
            "durability": _num(inv.get("durMin")),
            "broken": int(_num(inv.get("broken"), 0) or 0),
            "repair_cost": _num(inv.get("repairCost")),
            "session_loot": int(_num(inv.get("sessionLoot"), 0) or 0),
            "mats": mats,
            "members": members,
            "shared_quests": shared,
            "updated": _updated(c) or None,
        }
    except Exception:
        log.debug("leveling: summarize failed", exc_info=True)
        return None


def lines(summary: dict | None, max_quests: int = 6) -> list[str]:
    """Short French lines for the WoW page (empty list when there is nothing)."""
    if not summary:
        return []
    out: list[str] = []
    try:
        head = f"Niveau {summary.get('level') or '?'}"
        if summary.get("xp_pct") is not None:
            head += f" ({summary['xp_pct']} %)"
        if summary.get("zone"):
            head += f" — {summary['zone']}"
        out.append(head)
        if summary.get("rested_pct"):
            out.append(f"XP reposée : {summary['rested_pct']} % d'un niveau")
        if summary.get("money") is not None:
            out.append(f"Or : {format_money(summary['money'])}")
        if summary.get("train_pending"):
            out.append(f"À faire : maître de classe (niveau {int(summary['train_pending'])})")
        if summary.get("talent_points"):
            out.append(f"À faire : {summary['talent_points']} point(s) de talent")
        if summary.get("zone_warn"):
            out.append(f"Zone : {summary['zone_warn']}")
        if summary.get("hint"):
            out.append(f"Prochain coin conseillé : {summary['hint']}")
        for sk in summary.get("skills_behind") or []:
            out.append(f"Compétence en retard : {sk['name']} {sk['rank']}/{sk['max']}")
        if summary.get("bags_free") is not None:
            t = f"Sacs : {summary['bags_free']} place(s) libre(s)"
            if summary.get("bags_total"):
                t += f" sur {summary['bags_total']}"
            if summary.get("grey_count"):
                t += f" · {summary['grey_count']} gris ({format_money(summary['grey_value'])})"
            out.append(t)
        dur = summary.get("durability")
        if summary.get("broken"):
            out.append("Équipement cassé : répare !")
        elif dur is not None and dur <= 30:
            out.append(f"Équipement à {int(dur)} % : répare bientôt")
        if summary.get("session_loot"):
            out.append(f"Butin de la dernière session : {format_money(summary['session_loot'])}")
        quests = summary.get("quests") or []
        if quests:
            done = sum(1 for q in quests if q.get("done"))
            out.append(f"Quêtes : {len(quests)} ({done} à rendre)")
            for q in quests[:max_quests]:
                lvl = f"[{int(q['level'])}] " if q.get("level") else ""
                state = " ✓ à rendre" if q.get("done") else (f" — {q['obj'][0]}" if q.get("obj") else "")
                out.append(f"  {lvl}{q['title']}{state}")
        for name, m in sorted((summary.get("members") or {}).items()):
            t = f"Ami : {name}"
            if m.get("level"):
                t += f" niv {int(m['level'])}"
            if m.get("zone"):
                t += f", {m['zone']}"
            if m.get("far"):
                t += f" (~{int(m['far'] * 0.9144)} m)"
            out.append(t)
        for e in (summary.get("shared_quests") or [])[:4]:
            if e.get("need"):
                out.append(f"Groupe : « {e['title']} » — {' ; '.join(e['need'][:3])}")
    except Exception:
        log.debug("leveling: lines failed", exc_info=True)
    return out


def load_summary(path: str | Path | None, char: str | None = None) -> dict | None:
    """Summary from a ``SavedVariables/TreeAI.lua`` file (None when missing/invalid). Never raises."""
    if not path:
        return None
    try:
        from treeaicoach.wow import savedvars  # agent A's safe parser (no eval)
    except Exception:
        log.debug("leveling: savedvars parser unavailable", exc_info=True)
        return None
    try:
        return summarize(savedvars.read_db(path), char)
    except Exception:
        log.debug("leveling: load_summary failed", exc_info=True)
        return None
