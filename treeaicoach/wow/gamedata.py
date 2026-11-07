"""WoW Forever game data (class spells by level, items, zones) from the client tables.

Built offline by ``tools/wow_build_data.py`` into ``treeaicoach/wow/data/*.json.gz`` (see the README there:
Blizzard client DB2 tables via wago.tools, build in ``meta()``). Everything here is lazy, cached and never
raises: missing/corrupt data -> empty results.

Main helpers:
  new_spells(cls, from_lvl, to_lvl)   spells learned between two levels (inclusive)
  next_spells_text(cls, level)        « Niv. 28 : Piège de givre, Tir des arcanes (rang 4) » for the next levels
  item(id) / item_name(id) / find_items(text)
  item_score(id, cls, spec) / compare(a, b, cls, spec)   simple stat weights (documented in WEIGHTS)
  zone_name(id)
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import threading
import unicodedata
from typing import Any

log = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_CACHE: dict[str, Any] = {}
_LOCK = threading.Lock()

# ------------------------------------------------------------------------------------------- classes
CLASSES = ("WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID")
CLASS_FR = {"WARRIOR": "Guerrier", "PALADIN": "Paladin", "HUNTER": "Chasseur", "ROGUE": "Voleur",
            "PRIEST": "Prêtre", "SHAMAN": "Chaman", "MAGE": "Mage", "WARLOCK": "Démoniste", "DRUID": "Druide"}
CLASS_ID = {"WARRIOR": 1, "PALADIN": 2, "HUNTER": 3, "ROGUE": 4, "PRIEST": 5, "SHAMAN": 7, "MAGE": 8,
            "WARLOCK": 9, "DRUID": 11}
_ALIASES = {"guerriere": "WARRIOR", "chasseresse": "HUNTER", "voleuse": "ROGUE", "pretresse": "PRIEST",
            "chamane": "SHAMAN", "demonist": "WARLOCK", "hunt": "HUNTER", "chass": "HUNTER", "war": "WARRIOR",
            "lock": "WARLOCK", "drood": "DRUID", "pal": "PALADIN", "sham": "SHAMAN", "rogue": "ROGUE"}

STAT_FR = {"0": "Mana", "1": "Vie", "3": "Agilité", "4": "Force", "5": "Intelligence", "6": "Esprit",
           "7": "Endurance", "12": "Défense", "13": "Esquive", "14": "Parade", "15": "Blocage",
           "31": "Toucher", "32": "Critique", "36": "Hâte", "37": "Expertise", "38": "Puissance d'attaque",
           "39": "PA à distance", "41": "Soins", "42": "Dégâts des sorts", "43": "Mana/5 s",
           "45": "Puissance des sorts", "46": "Vie/5 s", "47": "Pénétration des sorts", "48": "Valeur de blocage",
           "50": "Armure bonus", "51": "Résistance au Feu", "52": "Résistance au Givre",
           "53": "Résistance au Sacré", "54": "Résistance à l'Ombre", "55": "Résistance à la Nature",
           "56": "Résistance aux Arcanes"}
QUALITY_FR = ("Médiocre", "Commun", "Inhabituel", "Rare", "Épique", "Légendaire", "Artefact", "Héritage")
INV_FR = {1: "Tête", 2: "Cou", 3: "Épaules", 4: "Chemise", 5: "Torse", 6: "Taille", 7: "Jambes", 8: "Pieds",
          9: "Poignets", 10: "Mains", 11: "Doigt", 12: "Bijou", 13: "Une main", 14: "Bouclier", 15: "Distance",
          16: "Dos", 17: "Deux mains", 18: "Sac", 19: "Tabard", 20: "Torse", 21: "Main droite",
          22: "Main gauche", 23: "Tenu en main gauche", 24: "Projectile", 25: "Armes de jet", 26: "Distance",
          28: "Relique"}
RANGED_INV = (15, 25, 26)
MELEE_WEAPON_INV = (13, 17, 21, 22)

# ------------------------------------------------------------------------------------------- weights
# Simple Classic-style stat weights, per stat point (stat ids above; "dps" = weapon DPS, "armor" = armour
# point). Values are relative to the main stat = 1.0. Rough community rules of thumb (1 AGI ≈ 2 AP for a
# hunter/rogue, 1 STR = 2 AP for warriors/paladins…); "rating" stats (31 hit, 32 crit) count per point.
# Weapon DPS only counts on the slot the spec actually uses (hunter: ranged; melee: melee weapons).
_PHYS = {"7": 0.35, "50": 0.02, "armor": 0.01, "31": 0.9, "32": 0.8, "36": 0.6}
WEIGHTS: dict[str, dict[str, dict[str, float]]] = {
    "HUNTER": {"*": {"3": 1.0, "39": 0.45, "38": 0.45, **_PHYS, "5": 0.15, "4": 0.05, "dps_ranged": 3.0,
                     "dps_melee": 0.3}},
    "ROGUE": {"*": {"3": 1.0, "4": 0.5, "38": 0.5, **_PHYS, "dps_melee": 3.0}},
    "WARRIOR": {"*": {"4": 1.0, "3": 0.6, "38": 0.5, **_PHYS, "dps_melee": 3.5},
                "protection": {"7": 1.0, "12": 0.8, "13": 0.8, "14": 0.7, "15": 0.4, "48": 0.3, "armor": 0.05,
                               "50": 0.05, "4": 0.5, "3": 0.5, "31": 0.4, "dps_melee": 1.0}},
    "PALADIN": {"*": {"4": 1.0, "3": 0.5, "5": 0.3, "38": 0.5, **_PHYS, "45": 0.4, "dps_melee": 3.5},
                "sacre": {"5": 1.0, "41": 0.7, "45": 0.8, "43": 1.5, "6": 0.3, "7": 0.25, "32": 0.5},
                "protection": {"7": 1.0, "12": 0.8, "4": 0.5, "45": 0.4, "armor": 0.05, "50": 0.05,
                               "48": 0.3, "dps_melee": 1.0}},
    "SHAMAN": {"*": {"5": 1.0, "45": 1.0, "42": 1.0, "6": 0.2, "7": 0.3, "32": 0.7, "31": 0.8, "43": 1.2},
               "amelioration": {"4": 1.0, "3": 0.6, "38": 0.5, "5": 0.2, **_PHYS, "dps_melee": 3.5},
               "restauration": {"5": 1.0, "41": 0.8, "45": 0.8, "43": 1.8, "6": 0.3, "7": 0.25, "32": 0.5}},
    "DRUID": {"*": {"5": 1.0, "45": 1.0, "42": 1.0, "6": 0.3, "7": 0.3, "32": 0.7, "31": 0.8, "43": 1.0},
              "farouche": {"3": 1.0, "4": 0.8, "38": 0.5, "7": 0.5, "armor": 0.03, "31": 0.9, "32": 0.8},
              "restauration": {"5": 1.0, "6": 0.6, "41": 0.8, "45": 0.8, "43": 1.5, "7": 0.25}},
    "PRIEST": {"*": {"5": 1.0, "6": 0.7, "41": 0.8, "45": 0.8, "43": 1.5, "7": 0.25, "32": 0.4},
               "ombre": {"5": 0.8, "45": 1.2, "42": 1.2, "6": 0.5, "7": 0.4, "31": 1.0, "32": 0.6}},
    "MAGE": {"*": {"5": 1.0, "45": 1.2, "42": 1.2, "6": 0.3, "7": 0.35, "31": 1.0, "32": 0.8, "43": 1.0}},
    "WARLOCK": {"*": {"5": 0.8, "7": 0.6, "45": 1.2, "42": 1.2, "6": 0.4, "31": 1.0, "32": 0.6}},
}
_SPEC_ALIASES = {"holy": "sacre", "sacré": "sacre", "prot": "protection", "tank": "protection",
                 "enhancement": "amelioration", "amélioration": "amelioration", "resto": "restauration",
                 "restoration": "restauration", "feral": "farouche", "combat farouche": "farouche",
                 "shadow": "ombre", "heal": "restauration", "soins": "restauration"}


def _fold(s: Any) -> str:
    t = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower().strip()
    return " ".join(t.split())


def class_token(cls: Any) -> str | None:
    """'Chasseur' / 'chasseresse' / 'HUNTER' / 3 -> 'HUNTER' (None if unknown)."""
    try:
        if isinstance(cls, int) or (isinstance(cls, str) and cls.isdigit()):
            n = int(cls)
            return next((c for c, v in CLASS_ID.items() if v == n), None)
        f = _fold(cls)
        if not f:
            return None
        for c in CLASSES:
            if f == c.lower() or f == _fold(CLASS_FR[c]):
                return c
        for k, c in _ALIASES.items():
            if f.startswith(k):
                return c
        for c in CLASSES:
            if f.startswith(_fold(CLASS_FR[c])[:5]):
                return c
    except Exception:
        log.debug("class_token failed", exc_info=True)
    return None


def _load(name: str) -> dict:
    with _LOCK:
        if name in _CACHE:
            return _CACHE[name]
        data: dict = {}
        try:
            with gzip.open(os.path.join(DATA_DIR, f"{name}.json.gz"), "rt", encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, dict):
                data = d
        except Exception:
            log.debug("gamedata: cannot load %s", name, exc_info=True)
        _CACHE[name] = data
        return data


def available() -> bool:
    return bool(_load("spells").get("classes")) and bool(_load("items").get("items"))


def meta() -> dict:
    return dict(_load("meta"))


# -------------------------------------------------------------------------------------------- spells
def class_spells(cls: Any) -> list[dict]:
    """All trainable spells of a class: [{level, id, name, rank, talent}] sorted by level."""
    try:
        c = class_token(cls)
        rows = _load("spells").get("classes", {}).get(c or "", [])
        return [{"level": r[0], "id": r[1], "name": r[2], "rank": r[3], "talent": bool(r[4])} for r in rows]
    except Exception:
        log.debug("class_spells failed", exc_info=True)
        return []


def new_spells(cls: Any, from_lvl: Any, to_lvl: Any = None, talents: bool = False) -> list[dict]:
    """Spells learned at levels from_lvl..to_lvl (inclusive). Talent-only ranks excluded unless asked."""
    try:
        lo = int(from_lvl)
        hi = int(to_lvl) if to_lvl is not None else lo
        return [s for s in class_spells(cls) if lo <= s["level"] <= hi and (talents or not s["talent"])]
    except Exception:
        log.debug("new_spells failed", exc_info=True)
        return []


def spell_label(s: dict) -> str:
    return f"{s['name']} (rang {s['rank']})" if s.get("rank") else str(s.get("name", ""))


def next_spells(cls: Any, level: Any, levels: int = 2) -> list[tuple[int, list[dict]]]:
    """The next ``levels`` levels (> level, ≤ 60) where the class learns something: [(lvl, spells)]."""
    try:
        cur = int(level)
        by: dict[int, list[dict]] = {}
        for s in new_spells(cls, cur + 1, 60):
            by.setdefault(s["level"], []).append(s)
        return [(lv, by[lv]) for lv in sorted(by)[:max(0, int(levels))]]
    except Exception:
        log.debug("next_spells failed", exc_info=True)
        return []


def next_spells_text(cls: Any, level: Any, levels: int = 2, max_per_level: int = 6) -> list[str]:
    """['Niv. 28 : Piège de givre, Tir des arcanes (rang 4), …', …] — short French lines."""
    out = []
    for lv, spells in next_spells(cls, level, levels):
        names = [spell_label(s) for s in spells[:max_per_level]]
        more = len(spells) - len(names)
        out.append(f"Niv. {lv} : " + ", ".join(names) + (f" (+{more})" if more > 0 else ""))
    return out


# --------------------------------------------------------------------------------------------- items
def item(item_id: Any) -> dict | None:
    """Item dict (FR name, quality, ilvl, req_level, class/subclass/inv_type, stats{name: n}, armor, dps…)."""
    try:
        d = _load("items")
        row = d.get("items", {}).get(str(int(item_id)))
        if not row:
            return None
        it = dict(zip(d.get("fields", []), row))
        it["id"] = int(item_id)
        it["stat_ids"] = dict(it.get("stats") or {})
        it["stats"] = {STAT_FR.get(k, f"stat {k}"): v for k, v in it["stat_ids"].items()}
        q = it.get("quality", 0)
        it["quality_name"] = QUALITY_FR[q] if isinstance(q, int) and 0 <= q < len(QUALITY_FR) else ""
        it["slot"] = INV_FR.get(it.get("inv_type", 0), "")
        it["starts_quest"] = bool(it.get("start_quest"))
        return it
    except Exception:
        log.debug("item failed", exc_info=True)
        return None


def item_name(item_id: Any) -> str | None:
    it = item(item_id)
    return it["name"] if it else None


def find_items(text: Any, limit: int = 10) -> list[dict]:
    """Items whose French name contains ``text`` (accent/case-insensitive), exact matches first."""
    try:
        q = _fold(text)
        if len(q) < 2:
            return []
        hits = []
        for k, row in _load("items").get("items", {}).items():
            n = _fold(row[0])
            if q in n:
                hits.append((n != q, len(n), int(k)))
        hits.sort()
        return [x for x in (item(h[2]) for h in hits[:max(0, int(limit))]) if x]
    except Exception:
        log.debug("find_items failed", exc_info=True)
        return []


def _resolve(x: Any) -> dict | None:
    if isinstance(x, dict):
        return x
    if isinstance(x, int) or (isinstance(x, str) and x.strip().isdigit()):
        return item(int(x))
    hits = find_items(x, 1)
    return hits[0] if hits else None


def weights_for(cls: Any, spec: Any = None) -> dict[str, float]:
    c = class_token(cls) or "HUNTER"
    table = WEIGHTS.get(c, {})
    s = _fold(spec)
    s = _SPEC_ALIASES.get(s, s)
    return dict(table.get(s) or table.get("*") or {})


def item_score(x: Any, cls: Any, spec: Any = None) -> float:
    """Weighted value of an item for a class/spec (0 when unknown)."""
    try:
        it = _resolve(x)
        if not it:
            return 0.0
        w = weights_for(cls, spec)
        stats = it.get("stat_ids") or {}
        score = sum(w.get(k, 0.0) * float(v) for k, v in stats.items())
        score += w.get("armor", 0.0) * float(it.get("armor") or 0)
        dps = float(it.get("dps") or 0)
        if dps:
            inv = it.get("inv_type")
            if inv in RANGED_INV:
                score += w.get("dps_ranged", 0.0) * dps
            elif inv in MELEE_WEAPON_INV:
                score += w.get("dps_melee", 0.0) * dps
        return round(score, 1)
    except Exception:
        log.debug("item_score failed", exc_info=True)
        return 0.0


def can_use(x: Any, cls: Any) -> bool:
    """Allowable-class mask check only (armour/weapon proficiencies are not modelled)."""
    it = _resolve(x)
    c = class_token(cls)
    if not it or not c:
        return True
    mask = it.get("allowable_class", -1)
    return mask in (-1, 0) or bool(mask & (1 << (CLASS_ID[c] - 1)))


def compare(item_a: Any, item_b: Any, cls: Any, spec: Any = None) -> dict:
    """Compare two items (ids, names or dicts). Returns {a, b, score_a, score_b, better: 'a'|'b'|None,
    delta, text}. ``text`` is a short French verdict, e.g. « Arc d'Ashwood est mieux pour toi (+4.5). »"""
    try:
        a, b = _resolve(item_a), _resolve(item_b)
        if not a or not b:
            return {"a": a, "b": b, "better": None, "delta": 0.0, "text": "Objet inconnu de la base."}
        sa, sb = item_score(a, cls, spec), item_score(b, cls, spec)
        ua, ub = can_use(a, cls), can_use(b, cls)
        if ua and not ub:
            sb = 0.0
        if ub and not ua:
            sa = 0.0
        delta = round(sa - sb, 1)
        if abs(delta) < 0.5:
            better, text = None, f"{a['name']} et {b['name']} se valent pour toi."
        else:
            win = a if delta > 0 else b
            better = "a" if delta > 0 else "b"
            text = f"{win['name']} est mieux pour toi (+{abs(delta):g})."
        return {"a": a, "b": b, "score_a": sa, "score_b": sb, "better": better, "delta": delta, "text": text}
    except Exception:
        log.debug("compare failed", exc_info=True)
        return {"better": None, "delta": 0.0, "text": ""}


# --------------------------------------------------------------------------------------------- zones
def zone_name(area_id: Any) -> str | None:
    try:
        row = _load("zones").get("zones", {}).get(str(int(area_id)))
        return row[0] if row else None
    except Exception:
        log.debug("zone_name failed", exc_info=True)
        return None


def zone_id(name: Any) -> int | None:
    """AreaTable id of a French zone name (top-level zone preferred)."""
    try:
        q = _fold(name)
        best = None
        for k, row in _load("zones").get("zones", {}).items():
            if _fold(row[0]) == q:
                cand = (row[1] != 0, int(k))
                best = cand if best is None or cand < best else best
        return best[1] if best else None
    except Exception:
        log.debug("zone_id failed", exc_info=True)
        return None


def game_data_context(cls: Any, level: Any) -> dict[str, Any]:
    """Short « données du jeu » block for the AI prompt ({} when nothing useful)."""
    try:
        if not class_token(cls) or level is None:
            return {}
        lines = next_spells_text(cls, int(float(level)), 2)
        return {"prochains_sorts": lines} if lines else {}
    except Exception:
        log.debug("game_data_context failed", exc_info=True)
        return {}
