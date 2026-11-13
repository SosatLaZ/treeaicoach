"""Gear planner, Python side (agent GEAR): « équipement » summary for the AI prompt + offline helpers.

Sources: the addon's ``summary.gear`` (Modules/Gear.lua, written to SavedVariables: per-slot score, next
upgrade with where/cost/effort, shopping list, hunter notes) and ``data/gear.json.gz`` (built by
``tools/wow_build_gear.py`` from the client tables: crafted items + skill, buy price, binding). The client
build has no JournalEncounter* tables, so boss / vendor / quest sources only exist at runtime (QuestieDB).
Never raises; pure functions.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import threading
from typing import Any

from treeaicoach.wow import gamedata

log = logging.getLogger(__name__)
_PATH = os.path.join(gamedata.DATA_DIR, "gear.json.gz")
_DATA: dict | None = None
_LOCK = threading.Lock()

# InventoryType -> slot group used by the offline planner
SLOT_OF_INV = {1: "Tête", 2: "Cou", 3: "Épaules", 5: "Torse", 20: "Torse", 6: "Taille", 7: "Jambes", 8: "Pieds",
               9: "Poignets", 10: "Mains", 11: "Doigt", 12: "Bijou", 16: "Dos", 13: "Main droite",
               21: "Main droite", 17: "Main droite", 14: "Main gauche", 22: "Main gauche", 23: "Main gauche",
               15: "Distance", 25: "Distance", 26: "Distance", 28: "Distance"}
# armour subclass -> level from which the class can wear it (classID 4)
ARMOR_LEVEL = {"HUNTER": {1: 1, 2: 1, 3: 40}, "SHAMAN": {1: 1, 2: 1, 3: 40, 6: 1},
               "WARRIOR": {1: 1, 2: 1, 3: 1, 4: 40, 6: 1}, "PALADIN": {1: 1, 2: 1, 3: 1, 4: 40, 6: 1},
               "ROGUE": {1: 1, 2: 1}, "DRUID": {1: 1, 2: 1}, "MAGE": {1: 1}, "PRIEST": {1: 1}, "WARLOCK": {1: 1}}
WEAPONS = {"HUNTER": {0, 1, 2, 3, 6, 7, 8, 10, 13, 15, 16, 18}, "WARRIOR": {0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 13, 15, 16, 18},
           "ROGUE": {2, 3, 4, 7, 13, 15, 16, 18}, "MAGE": {7, 10, 15, 19}, "WARLOCK": {7, 10, 15, 19},
           "PRIEST": {4, 10, 15, 19}, "DRUID": {4, 5, 10, 13, 15}, "PALADIN": {0, 1, 4, 5, 6, 7, 8},
           "SHAMAN": {0, 1, 4, 5, 10, 13, 15}}


# where the addon got the spec from (Modules/GearSpec.lua: T.Gear.Spec().source)
SPEC_SOURCE_FR = {"api": "lue dans les talents", "spells": "déduite des sorts connus", "manual": "choisie par le joueur"}


def data() -> dict:
    global _DATA
    with _LOCK:
        if _DATA is None:
            try:
                with gzip.open(_PATH, "rt", encoding="utf-8") as fh:
                    d = json.load(fh)
                _DATA = d if isinstance(d, dict) else {}
            except Exception:
                log.debug("gear data unavailable", exc_info=True)
                _DATA = {}
        return _DATA


def crafted(item_id: Any) -> dict | None:
    """{'prof': 'Couture', 'skill': 75} when the item is made by a profession, else None."""
    try:
        d = data()
        v = d.get("crafted", {}).get(str(int(item_id)))
        if not v:
            return None
        return {"prof": d.get("profs", {}).get(str(v[0]), "artisanat"), "skill": int(v[1])}
    except Exception:
        return None


def usable(it: dict, cls: str, level: int) -> bool:
    """Class mask + armour type (mail at 40 for hunters/shamans, plate at 40) + weapon proficiency."""
    if not gamedata.can_use(it, cls):
        return False
    c, sub = it.get("class"), it.get("subclass")
    if c == 4 and sub and it.get("inv_type") != 16:
        need = ARMOR_LEVEL.get(cls, {}).get(sub)
        return need is not None and level >= need
    if c == 2:
        return sub in WEAPONS.get(cls, set())
    return True


def upgrades(cls: Any, level: Any, equipped: dict[str, Any] | None = None, spec: Any = None,
             horizon: int = 3, per_slot: int = 1) -> dict[str, list[dict]]:
    """Offline best items per slot group for a class/level (client stats only, no live sources).
    ``equipped`` = {slot group: item id}; returns {slot: [{id, name, req, score, gain_pct, crafted}]}."""
    out: dict[str, list[dict]] = {}
    try:
        c = gamedata.class_token(cls)
        lv = int(float(level))
        if not c:
            return {}
        cur = {k: gamedata.item_score(v, c, spec) for k, v in (equipped or {}).items() if v}
        items = gamedata._load("items")
        F = items.get("fields", [])
        best: dict[str, list[tuple]] = {}
        for k, row in items.get("items", {}).items():
            it = dict(zip(F, row))
            slot = SLOT_OF_INV.get(it.get("inv_type"))
            if not slot or it.get("class") not in (2, 4) or it.get("quality", 0) < 2:
                continue
            if not (lv - 12 <= it.get("req_level", 0) <= lv + horizon):
                continue
            it["id"] = int(k)
            if not usable(it, c, lv + horizon):
                continue
            sc = gamedata.item_score({**it, "stat_ids": it.get("stats") or {}}, c, spec)
            old = cur.get(slot, 0.0)
            if sc <= old * 1.03:
                continue
            best.setdefault(slot, []).append((sc, it))
        for slot, lst in best.items():
            lst.sort(key=lambda t: (-t[0], t[1]["id"]))
            old = cur.get(slot, 0.0)
            out[slot] = [{"id": it["id"], "name": it["name"], "req": it["req_level"], "score": sc,
                          "gain_pct": round((sc - old) / old * 100) if old > 0 else None,
                          "crafted": crafted(it["id"])} for sc, it in lst[:max(1, per_slot)]]
    except Exception:
        log.debug("gear upgrades failed", exc_info=True)
    return out


def _money(c: Any) -> str:
    try:
        c = int(c or 0)
    except (TypeError, ValueError):
        return "?"
    g, s = c // 10000, (c // 100) % 100
    return f"{g} po {s} pa" if g else f"{s} pa"


def gear_summary(db_or_char: Any) -> dict:
    """``summary.gear`` from TreeAIDB / a char entry / a summary ({} if absent)."""
    try:
        d = db_or_char if isinstance(db_or_char, dict) else {}
        if isinstance(d.get("gear"), dict):
            return d["gear"]
        if isinstance(d.get("summary"), dict):
            return gear_summary(d["summary"])
        chars = d.get("chars")
        if isinstance(chars, dict):
            for c in chars.values():
                g = gear_summary(c) if isinstance(c, dict) else {}
                if g:
                    return g
    except Exception:
        log.debug("gear_summary failed", exc_info=True)
    return {}


def gear_context(db_or_char: Any, limit: int = 6) -> dict:
    """Compact French « équipement » block for the AI prompt ({} when the addon wrote nothing)."""
    try:
        g = gear_summary(db_or_char)
        if not g:
            return {}
        out: dict[str, Any] = {}
        if g.get("priority"):
            out["priorite_stats"] = str(g["priority"])[:220]
        # (5.0 GEAR) the spec the addon scores gear for, and where it comes from (talents read / spells / chosen)
        if g.get("specName"):
            src = SPEC_SOURCE_FR.get(str(g.get("specSource")), "")
            out["spe"] = str(g["specName"])[:60] + (f" ({src})" if src else "")
        ups = []
        for row in g.get("slots") or []:
            n = row.get("next") if isinstance(row, dict) else None
            if not isinstance(n, dict):
                continue
            gain = n.get("gain")
            gain_t = "nouvel emplacement" if (gain or 0) >= 999 else f"+{gain} %"
            cost = f", {_money(n['cost'])}" if n.get("cost") else ""
            ups.append((gain or 0, f"{row.get('slot')}: {n.get('name')} ({gain_t}) — {n.get('where')}{cost}"))
        ups.sort(key=lambda t: -t[0])
        if ups:
            out["ameliorations"] = [t for _, t in ups[:limit]]
        shop = g.get("shopping") or []
        if shop:
            out["liste_de_courses"] = f"{len(shop)} objet(s), {_money(g.get('total'))} : " + ", ".join(
                str(s.get("name")) for s in shop[:5] if isinstance(s, dict))
        h = g.get("hunter")
        if isinstance(h, dict):
            bits = []
            if h.get("ranged"):
                bits.append(f"arme à distance {h['ranged']} ({h.get('dps') or '?'} DPS, {h.get('speed') or '?'} s)")
            if h.get("ammo"):
                bits.append(f"munitions {h['ammo']}" + ("" if h.get("ammoOK") else " MANQUANTES"))
            if h.get("mail"):
                bits.append(str(h["mail"]))
            if bits:
                out["chasseur"] = " ; ".join(bits)
        return out
    except Exception:
        log.debug("gear_context failed", exc_info=True)
        return {}
