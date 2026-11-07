"""Chasseur : nourriture de familier, lecture de l'instantané addon, conseils FR.

Source des données : ``TreeAIDB.chars[<clé perso>].summary.hunter`` écrit par
``wow_addon/TreeAI/Modules/Hunter.lua`` (message SUMMARY du cœur, déconnexion, /reload).
Schéma d'un instantané ::

    {t, level, clips,
     pet = {level, family, dead, happiness(1-3), damagePct, loyaltyRate, loyalty,
            tpFree, diet = ["meat", ...], food = itemID|nil},
     ammo = {kind = "arrow"|"bullet"|nil, total, equipped, quiver, mismatch}}

Fonctions pures, ne lèvent jamais d'exception.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

log = logging.getLogger(__name__)

# (itemID, régime, niveau d'objet approx.) — identique à Hunter.lua (vérifié par test).
FOODS: tuple[tuple[int, str, int], ...] = (
    (117, "meat", 1), (2287, "meat", 5), (3770, "meat", 15), (3771, "meat", 25),
    (4599, "meat", 35), (8952, "meat", 45),
    (769, "meat", 5), (2672, "meat", 5), (2673, "meat", 10), (2677, "meat", 12),
    (729, "meat", 12), (1015, "meat", 15), (2886, "meat", 15), (5465, "meat", 10),
    (1081, "meat", 18), (2924, "meat", 20), (3173, "meat", 20), (3731, "meat", 25),
    (3712, "meat", 30), (3730, "meat", 30), (12037, "meat", 35), (12184, "meat", 40),
    (12203, "meat", 35), (12205, "meat", 42), (12208, "meat", 42),
    (2681, "meat", 5), (2680, "meat", 10), (724, "meat", 12), (2888, "meat", 15),
    (3220, "meat", 20), (3662, "meat", 22), (3726, "meat", 30), (3727, "meat", 30),
    (12209, "meat", 37), (12210, "meat", 42),
    (787, "fish", 1), (4592, "fish", 5), (4593, "fish", 15), (4594, "fish", 25),
    (21552, "fish", 35), (8957, "fish", 45),
    (6291, "fish", 5), (6303, "fish", 5), (6289, "fish", 10), (6308, "fish", 20),
    (6290, "fish", 5), (4603, "fish", 35), (5095, "fish", 25), (6316, "fish", 15),
    (13930, "fish", 45), (13933, "fish", 55),
    (4540, "bread", 1), (4541, "bread", 5), (4542, "bread", 15), (4544, "bread", 25),
    (4601, "bread", 35), (8950, "bread", 45),
    (2070, "cheese", 1), (414, "cheese", 5), (422, "cheese", 15), (1707, "cheese", 25),
    (3927, "cheese", 35), (8932, "cheese", 45),
    (4536, "fruit", 1), (4537, "fruit", 5), (4538, "fruit", 15), (4539, "fruit", 25),
    (4602, "fruit", 35), (8953, "fruit", 45),
    (4604, "fungus", 1), (4605, "fungus", 5), (4606, "fungus", 15), (4607, "fungus", 25),
    (4608, "fungus", 35), (8948, "fungus", 45),
)
FOOD_BY_ID = {f[0]: f for f in FOODS}

DIET_FR = {"meat": "viande", "fish": "poisson", "bread": "pain", "cheese": "fromage",
           "fruit": "fruit", "fungus": "champignon"}

# Régimes typiques par famille (Classic) — pour conseiller avant d'apprivoiser.
FAMILY_DIETS: dict[str, tuple[str, ...]] = {
    "bear": ("meat", "fish", "bread", "cheese", "fruit", "fungus"),
    "boar": ("meat", "fish", "bread", "cheese", "fruit", "fungus"),
    "cat": ("meat", "fish"), "wolf": ("meat",), "raptor": ("meat",), "hyena": ("meat", "fruit"),
    "crab": ("fish", "bread", "fungus"), "crocolisk": ("meat", "fish"), "gorilla": ("fruit", "fungus"),
    "owl": ("meat",), "carrion bird": ("meat", "fish"), "bat": ("fruit", "fungus"),
    "spider": ("meat",), "scorpid": ("meat",), "turtle": ("fish", "fruit", "fungus"),
    "tallstrider": ("fruit", "fungus", "cheese"), "wind serpent": ("fish", "bread", "cheese"),
}

HAPPINESS_FR = {1: "mécontent", 2: "content", 3: "heureux"}


def pick_food(bag_ids: Iterable[int] | None, diet: Iterable[str] | None,
              pet_level: int | None) -> tuple[int | None, str | None]:
    """Même règle que Hunter.lua : la moins chère à effet plein (>= niv-10),
    sinon la plus haute encore mangeable (>= niv-30). Renvoie (itemID, "full"|"partial")."""
    try:
        diet_set = set(diet or ())
        lvl = int(pet_level or 1)
        full = ok = None
        for iid in bag_ids or ():
            f = FOOD_BY_ID.get(int(iid))
            if not f or f[1] not in diet_set:
                continue
            if f[2] >= lvl - 10:
                if full is None or f[2] < full[2]:
                    full = f
            elif f[2] >= lvl - 30:
                if ok is None or f[2] > ok[2]:
                    ok = f
        best = full or ok
        if best is None:
            return None, None
        return best[0], ("full" if full else "partial")
    except Exception as exc:  # noqa: BLE001
        log.debug("pick_food: %s", exc)
        return None, None


def _as_list(v: Any) -> list:
    """Tableau Lua -> liste (le parseur peut rendre une liste ou un dict {1: .., 2: ..})."""
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return [v[k] for k in sorted(k for k in v if isinstance(k, int))]
    return []


def load_snapshots(treeaidb: Any) -> dict[str, dict]:
    """{clé perso: instantané} depuis ``chars[*].summary.hunter`` d'un TreeAIDB parsé (dict)."""
    out: dict[str, dict] = {}
    try:
        for key, char in (treeaidb.get("chars") or {}).items():
            snap = (char.get("summary") or {}).get("hunter") if isinstance(char, dict) else None
            if isinstance(snap, dict):
                out[str(key)] = snap
    except Exception as exc:  # noqa: BLE001
        log.debug("load_snapshots: %s", exc)
    return out


def latest_snapshot(treeaidb: Any) -> dict | None:
    snaps = load_snapshots(treeaidb)
    if not snaps:
        return None
    return max(snaps.values(), key=lambda s: s.get("t") if isinstance(s.get("t"), (int, float)) else 0)


def _num(v: Any) -> float | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def hunter_tips(snap: dict | None) -> list[str]:
    """Conseils courts en français, du plus urgent au moins urgent."""
    if not isinstance(snap, dict):
        return []
    tips: list[tuple[int, str]] = []
    try:
        level = int(_num(snap.get("level")) or 0)
        pet = snap.get("pet")
        if isinstance(pet, dict):
            if pet.get("dead"):
                tips.append((3, "Familier mort : Ressusciter le familier avant de reprendre."))
            h, loy = pet.get("happiness"), _num(pet.get("loyaltyRate"))
            if h == 1 or (loy is not None and loy < 0):
                tips.append((3, "Familier mécontent : 75 % de dégâts et il perd de la loyauté. Nourris-le."))
            elif h == 2:
                tips.append((1, "Familier content : nourris-le pour passer à heureux (+25 % de dégâts)."))
            diet = [str(d) for d in _as_list(pet.get("diet"))]
            if not pet.get("food") and diet:
                tips.append((2, "Aucune nourriture adaptée dans les sacs (régime : "
                             + ", ".join(DIET_FR.get(d, d) for d in diet) + ")."))
            tp = _num(pet.get("tpFree")) or 0
            if tp > 0:
                tips.append((1, f"{int(tp)} point(s) d'entraînement libres pour ton familier."))
            plvl = _num(pet.get("level"))
            if plvl is not None and level and plvl < level - 2:
                tips.append((1, f"Familier niveau {int(plvl)} (toi {level}) : garde-le actif, il rattrape vite."))
        elif level >= 10 and pet is None:
            tips.append((2, "Pas de familier actif : appelle-le, il fait une grosse part de tes dégâts."))
        ammo = snap.get("ammo")
        if isinstance(ammo, dict) and ammo.get("kind") in ("arrow", "bullet"):
            word = "flèches" if ammo["kind"] == "arrow" else "balles"
            total = int(_num(ammo.get("total")) or 0)
            if ammo.get("mismatch"):
                tips.append((3, f"Mauvaises munitions équipées : il te faut des {word}."))
            if total < 50:
                tips.append((3, f"Plus que {total} {word} : réapprovisionne-toi."))
            elif total < 200:
                tips.append((2, f"Munitions basses : {total} {word}."))
            if not ammo.get("quiver") and level >= 10:
                tips.append((1, "Équipe un carquois / une giberne : tir plus rapide."))
        clips = _num(snap.get("clips")) or 0
        if clips >= 5:
            tips.append((1, f"{int(clips)} tirs auto retardés par Visée/Flèches multiples : "
                            "lance-les juste après un tir."))
    except Exception as exc:  # noqa: BLE001
        log.debug("hunter_tips: %s", exc)
    tips.sort(key=lambda t: -t[0])
    return [t for _, t in tips]
