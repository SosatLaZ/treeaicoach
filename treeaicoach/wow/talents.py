"""WoW Forever talents for the « Hey coach, quels talents ? » answers.

Reads ``treeaicoach/wow/data/talents.json.gz`` (trees + validated levelling builds, built by
``tools/wow_build_data.py`` from the client tables and ``talent_builds.py``) and the addon's
``summary.talents`` (module Talents: points per tree, free points, current build, next point, ranks).
Never raises: missing data -> empty results.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
from typing import Any

log = logging.getLogger(__name__)

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "talents.json.gz")
_CACHE: dict[str, Any] = {}
_FR = {"guerrier": "WARRIOR", "paladin": "PALADIN", "chasseur": "HUNTER", "voleur": "ROGUE", "pretre": "PRIEST",
       "prêtre": "PRIEST", "chaman": "SHAMAN", "mage": "MAGE", "demoniste": "WARLOCK", "démoniste": "WARLOCK",
       "druide": "DRUID"}


def data() -> dict[str, Any]:
    if "d" not in _CACHE:
        try:
            with gzip.open(DATA, "rt", encoding="utf-8") as fh:
                _CACHE["d"] = json.load(fh)
        except Exception:
            log.debug("talents data unavailable", exc_info=True)
            _CACHE["d"] = {}
    return _CACHE["d"]


def class_token(cls: Any) -> str:
    s = str(cls or "").strip()
    if s.upper() in data().get("classes", {}) or s.upper() in {"WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST",
                                                                 "SHAMAN", "MAGE", "WARLOCK", "DRUID"}:
        return s.upper()
    return _FR.get(s.lower(), "")


def builds_for(cls: Any) -> list[dict[str, Any]]:
    c = class_token(cls)
    return [b for b in data().get("builds", []) if b.get("class") == c]


def _ranks(text: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in str(text or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                out[k.strip()] = int(v)
            except ValueError:
                pass
    return out


def next_points(build: dict[str, Any], ranks: dict[str, int] | None = None, n: int = 3) -> list[str]:
    """The next ``n`` points of a build for the given ranks ("tab:tier:col" -> rank), as French lines."""
    ranks = dict(ranks or {})
    out: list[str] = []
    for s in build.get("steps", []):
        k = f"{s['tab']}:{s['tier']}:{s['col']}"
        while ranks.get(k, 0) < s["rank"] and len(out) < n:
            ranks[k] = ranks.get(k, 0) + 1
            out.append(f"{s['name']} {ranks[k]}/{s['rank']} : {s['why']}")
        if len(out) >= n:
            break
    return out


def readable(summary: Any, level: Any) -> bool:
    """Can the addon read the talent tree? WoW: Forever has no talent API (GetTalentInfo, UnitCharacterPoints…):
    the addon says ``readable = false``; older saves show it as no point spent nor free from level 10 on."""
    t = summary if isinstance(summary, dict) else {}
    if t.get("readable") is not None:
        return bool(t.get("readable"))
    try:
        lvl = int(float(level or 0))
    except (TypeError, ValueError):
        lvl = 0
    spent = sum(int(x) for x in re.findall(r"\d+", str(t.get("points") or "")))
    return lvl < 10 or spent > 0 or bool(t.get("unspent"))


def by_level(build: dict[str, Any], level: int, n: int = 3) -> list[str]:
    """The build's points for the next ``n`` levels (one point per level from 10), as French lines."""
    pts: list[str] = []
    for s in build.get("steps", []):
        for r in range(1, int(s["rank"]) + 1):
            pts.append(f"{s['name']} {r}/{s['rank']} : {s['why']}")
    first = max(0, level - 9)                     # points of levels 10..level are behind
    return [f"niveau {i + 10} : {pts[i]}" for i in range(first, min(len(pts), first + n))]


def talents_context(cls: Any, level: Any, summary: Any = None) -> dict[str, Any]:
    """Compact facts for the AI prompt: builds solo/groupe with their first picks or the next points, free
    points, dual spec plan, respec estimate. (4.0 QA) When the tree cannot be read (WoW: Forever), never a « next
    talent » from empty ranks: the guide level by level instead, said as such. {} when unknown."""
    try:
        bs = builds_for(cls)
        if not bs:
            return {}
        lvl = int(float(level or 0))
        t = summary if isinstance(summary, dict) else {}
        ranks = _ranks(t.get("ranks"))
        out: dict[str, Any] = {}
        known = readable(t, lvl)
        if not known:
            out["lecture"] = ("TreeAI ne peut pas lire tes talents sur WoW Forever : ce qui suit est le guide du "
                              "parcours niveau par niveau, pas ton arbre")
        if t.get("points") and known:
            out["points"] = t["points"]
        if t.get("unspent"):
            out["points_libres"] = t["unspent"]
        for b in bs:
            key = "solo" if b.get("role") == "solo" else "groupe"
            out[key] = f"{b['name']} ({b['split']}) — {b.get('summary', '')}"
            if not known:
                if key == "solo" or t.get("build") == b.get("id"):
                    guide = by_level(b, lvl)
                    if guide:
                        out[f"guide_{key}"] = guide
                continue
            nxt = next_points(b, ranks, 3 if key == "solo" or t.get("build") == b.get("id") else 2)
            if nxt:
                out[f"prochains_{key}"] = nxt
        if t.get("next") and known:
            nx = t["next"]
            out["prochain_point"] = f"{nx.get('name')} ({nx.get('rank')}/{nx.get('max')}) : {nx.get('why')}"
        if lvl >= 30:
            from treeaicoach.wow import talent_builds  # noqa: PLC0415

            d = talent_builds.DUAL.get(class_token(cls))
            if d:
                out["double_spe"] = f"Niveau 40, 50 po : {d['text']}"
        if t.get("respec"):
            out["reinitialisation"] = t["respec"]
        out["note"] = "Talents de Forever retravaillés ; TreeAI ne dépense jamais de point (touche N)."
        return out
    except Exception:
        log.debug("talents_context failed", exc_info=True)
        return {}
