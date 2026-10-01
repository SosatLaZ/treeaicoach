"""Offline build advice from the player's own state + public enemy info (Live Client Data API).

Riot allows build recommendations computed from the player's own state; enemy champions and
items are public (Tab scoreboard). Everything here is deterministic, pure Python and never
raises from its public functions.

* :func:`enemy_profile` - damage share (AD / AP / true), healers, heavy CC, tanks, burst,
  crit carries, shields, stealth, fed enemies (weighted by their kills / item gold).
* :func:`recommend` - the best next item for my champion class with a short French reason
  ("Prochain objet : Rappel mortel — Soraka et Aatrox se soignent beaucoup.") and the
  "achat immédiat" plan: exact components affordable now (:func:`plan_purchase`).
* :class:`ItemAdvisor` - when to say it (death, back in base / purchase, level 6/11/16, an
  enemy becoming fed) with strict anti-spam: the same recommendation is never repeated within
  :data:`REPEAT_S` unless the situation changed. Text only by default (HUD + toast).

The build tables (:data:`CORE`, :data:`NEED_ITEMS`, :data:`SAME_NEED`, :data:`BOOTS_CLASS`,
:data:`BOOTS_VS`, :data:`START_ITEMS`, :data:`SUPPORT_UPGRADE`, :data:`CHAMPION_BUILDS`) come from
``assets/item_builds.json`` (``tools/fetch_builds.py``: expert preference lists checked against the
item data - sold on the Rift, kind, stats, semantic flags); the constants written below are the
fallback when that file is missing. Items unknown to / not sold by the live item data (runtime
Data Dragon refresh) are skipped at advice time, so a removed item is never named.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable

log = logging.getLogger(__name__)

REPEAT_S = 300.0          # same recommendation not repeated within 5 min (unless situation changed)
MIN_GAP_S = 45.0          # between two advices (death / base visits only need DEATH_GAP_S)
DEATH_GAP_S = 20.0
BASE_GAP_S = 90.0         # one shopping advice per base visit (buying changes the inventory: not a new moment)
SPIKE_LEVELS = (6, 11, 16)
MIN_GT = 90.0             # no advice before 1:30
NEED_MIN = 1.0            # severity needed for a counter item

# ----------------------------------------------------------------------------- champions
AP = frozenset(
    "Ahri Akali Alistar Amumu Anivia Annie AurelionSol Aurora Azir Bard Brand Braum Cassiopeia Chogath "
    "Diana Ekko Elise Evelynn Fiddlesticks Fizz Galio Gragas Gwen Heimerdinger Hwei Ivern Janna Karma "
    "Karthus Kassadin Katarina Kayle Kennen Leblanc Leona Lillia Lissandra Lulu Lux Malphite Malzahar "
    "Maokai Mel Milio Mordekaiser Morgana Nami Nautilus Neeko Nidalee Nunu Orianna Rakan Rammus Rell "
    "Renata Rumble Ryze Sejuani Seraphine Singed Sona Soraka Swain Sylas Syndra TahmKench Taliyah Taric "
    "Teemo Thresh TwistedFate Veigar Velkoz Vex Viktor Vladimir Xerath Yuumi Zac Ziggs Zilean Zoe Zyra".split())
MIXED = frozenset("Corki Kaisa KogMaw Varus Jax Shaco Volibear Udyr Warwick Shyvana Ornn Skarner Shen".split())
TRUE_DMG = frozenset("Vayne Fiora Camille Gwen MasterYi Olaf Darius Garen".split())
HEALERS = frozenset(
    "Aatrox Soraka Yuumi Sona Nami Vladimir Sylas Warwick DrMundo Fiddlesticks Swain Briar Illaoi Olaf "
    "Volibear Kayn Trundle Milio Taric Seraphine Zac Gwen Irelia Samira Aphelios Fiora Udyr Maokai "
    "Ivern Viego Belveth Nidalee Rakan Bard Ekko XinZhao Nasus Renata Senna Zaahen Locke".split())
STRONG_HEALERS = frozenset("Soraka Yuumi Aatrox Vladimir DrMundo Sylas Warwick Swain Briar Illaoi Zaahen".split())
HEAVY_CC = frozenset(
    "Leona Nautilus Morgana Lissandra Sejuani Amumu Rell Thresh Ashe Malzahar Skarner Annie Veigar Zyra "
    "Lux Maokai Rammus Alistar Blitzcrank Vi Warwick Ornn Chogath Galio Braum Poppy JarvanIV Zac "
    "Fiddlesticks Neeko Seraphine Sett Pantheon Nunu Swain XinZhao Cassiopeia Kennen Varus Nami Bard "
    "Rakan Lulu Mordekaiser TahmKench Hecarim Sion Malphite Gragas Diana Shen MonkeyKing Yasuo Yone "
    "Elise Ahri Syndra Viktor Anivia Ryze Lillia Vex Jhin Camille Riven Udyr".split())
SHIELDS = frozenset(
    "Lulu Janna Karma Sett Seraphine Lux Orianna Shen Riven Rakan Sion Ivern Renata Milio Yasuo Yone "
    "Camille Mordekaiser Skarner Taric Morgana Rell Sona Diana Rumble TahmKench Poppy Annie Urgot Garen "
    "Ekko Udyr Vi Vex".split())
BURST = frozenset("Syndra Veigar Annie Brand Lux Viktor Ahri Vex Zoe Hwei Mel Neeko Karthus Leblanc".split())
NOT_CRIT = frozenset("KogMaw Varus Kindred Ezreal Corki Senna Kalista Vayne Smolder Kaisa Azir".split())
CRIT_EXTRA = frozenset("Yasuo Yone Tryndamere Gangplank MasterYi Nilah".split())

CLASSES = ("marksman", "mage", "assassin_ad", "assassin_ap", "fighter", "fighter_ap", "tank",
           "support_tank", "enchanter")

_TAGS: dict[str, tuple[str, ...]] | None = None
_lock = threading.Lock()


def _reset_tables() -> None:
    """Data Dragon refresh (:mod:`treeaicoach.game_data`): reload the item / tag tables."""
    global _TAGS, _ITEMS
    with _lock:
        _TAGS = None
        _ITEMS = None


def champion_tags(alias: str) -> tuple[str, ...]:
    """Data Dragon tags of a champion (runtime-refreshed data, else the bundled index; ``()`` if unknown)."""
    global _TAGS
    with _lock:
        if _TAGS is None:
            tags: dict[str, tuple[str, ...]] = {}
            try:
                from treeaicoach import game_data

                data = game_data.champions_data()
                for c in data.get("champions") or []:
                    tags[str(c.get("alias"))] = tuple(str(t) for t in (c.get("tags") or ()))
            except Exception:
                log.warning("Champion index unavailable for itemization", exc_info=True)
            _TAGS = tags
    return _TAGS.get(str(alias or ""), ())


def _meta(alias: str) -> Any:
    try:
        from treeaicoach.meta import profile

        return profile(alias)
    except Exception:
        return None


def _damage_type(alias: str) -> str:
    """"P" / "M" / "X": the champion profile (official Riot client damage type, curated mixed
    damage) first, the curated :data:`AP` / :data:`MIXED` lists for champions without a profile."""
    m = _meta(alias)
    if m is not None and m.known and m.damage in ("P", "M", "X"):
        return m.damage
    return "M" if alias in AP else "X" if alias in MIXED else "P"


def damage_split(alias: str) -> tuple[float, float, float]:
    """(physical, magic, true) share of a champion's damage (sums to 1)."""
    dt = _damage_type(alias)
    ad, ap = (0.1, 0.9) if dt == "M" else (0.5, 0.5) if dt == "X" else (0.9, 0.1)
    tr = 0.25 if alias in TRUE_DMG else 0.0
    return ad * (1 - tr), ap * (1 - tr), tr


def champion_class(alias: str, role: str | None = None) -> str:
    over = (CHAMPION_BUILDS.get(str(alias or "")) or {}).get("class")
    if over in CLASSES:
        return str(over)
    tags = champion_tags(alias)
    primary = tags[0] if tags else "Fighter"
    ap = alias in AP or _damage_type(alias) == "M"
    if alias == "Pyke":
        return "assassin_ad"
    if role == "UTILITY" or primary == "Support":
        if "Tank" in tags:
            return "support_tank"
        if primary in ("Support",) or (role == "UTILITY" and primary not in ("Mage", "Marksman")):
            return "enchanter"
    if primary == "Marksman":
        return "mage" if ap else "marksman"
    if primary == "Mage":
        return "mage" if ap or alias not in MIXED else "marksman"
    if primary == "Assassin":
        return "assassin_ap" if ap else "assassin_ad"
    if primary == "Tank":
        return "tank"
    return "fighter_ap" if ap else "fighter"


# ----------------------------------------------------------------------------- items
@dataclass(frozen=True)
class Item:
    id: int
    name: str
    gold: int
    base: int
    kind: str
    tags: tuple[str, ...]
    parts: tuple[int, ...]
    into: tuple[int, ...]
    rift: bool


_ITEMS: dict[int, Item] | None = None


def load_items(data: dict | None = None) -> dict[int, Item]:
    """``id -> Item`` from the item data (cached), or from ``data`` (tests). Never raises.

    The data is :func:`treeaicoach.game_data.items_data`: the Data Dragon table refreshed at
    runtime (new / renamed items, prices of the live patch), else the bundled ``assets/items.json``."""
    global _ITEMS
    if data is None and _ITEMS is not None:
        return _ITEMS
    table: dict[int, Item] = {}
    cache = data is None
    try:
        if data is None:
            from treeaicoach import game_data

            data = game_data.items_data()
        for k, v in (data.get("items") or {}).items():
            try:
                table[int(k)] = Item(int(k), str(v.get("n") or ""), int(v.get("g") or 0), int(v.get("b") or 0),
                                     str(v.get("k") or "other"), tuple(v.get("t") or ()),
                                     tuple(int(x) for x in v.get("f") or ()), tuple(int(x) for x in v.get("i") or ()),
                                     bool(v.get("p", 1)))
            except (TypeError, ValueError, AttributeError):
                continue
    except Exception:
        log.warning("Item table unavailable (assets/items.json)", exc_info=True)
    if cache:
        _ITEMS = table
    return table


def _remaining(item_id: int, pool: list[int], items: dict[int, Item]) -> int:
    """Gold still needed for ``item_id`` given owned items ``pool`` (consumed in place)."""
    if item_id in pool:
        pool.remove(item_id)
        return 0
    it = items.get(item_id)
    if it is None:
        return 0
    if not it.parts:
        return it.gold
    return max(0, it.base) + sum(_remaining(p, pool, items) for p in it.parts)


def remaining_cost(item_id: int, owned: Iterable[int], items: dict[int, Item] | None = None) -> int:
    items = items if items is not None else load_items()
    return _remaining(int(item_id), [int(x) for x in owned or ()], items)


def plan_purchase(item_id: int, owned: Iterable[int], gold: float,
                  items: dict[int, Item] | None = None) -> tuple[list[int], bool]:
    """Items to buy now towards ``item_id`` with ``gold``: ``(ids, completes)``. Greedy, exact prices."""
    items = items if items is not None else load_items()
    pool = [int(x) for x in owned or ()]
    budget = [int(max(0.0, gold if math.isfinite(float(gold)) else 0.0))]
    buys: list[int] = []

    def buy(iid: int) -> bool:
        p = list(pool)
        cost = _remaining(iid, p, items)
        if cost == 0 and iid not in pool and iid not in items:
            return False
        if cost <= budget[0]:
            budget[0] -= cost
            pool[:] = p
            buys.append(iid)
            return True
        it = items.get(iid)
        if it is None:
            return False
        p2 = list(pool)
        missing = []
        for part in it.parts:
            if part in p2:
                p2.remove(part)
            else:
                missing.append(part)
        for part in sorted(missing, key=lambda x: -remaining_cost(x, pool, items)):
            buy(part)
        return False

    done = buy(int(item_id))
    return buys, done


# counter items per need and class (preference order; first one not owned is chosen)
NEED_ITEMS: dict[str, dict[str, tuple[int, ...]]] = {
    "antiheal": {"marksman": (3033, 3123), "assassin_ad": (6609, 3123), "fighter": (6609, 3123),
                 "mage": (3165, 3916), "assassin_ap": (3165, 3916), "fighter_ap": (3165, 3916),
                 "tank": (3075, 3076), "support_tank": (3075, 3076), "enchanter": (3916,)},
    "magic": {"marksman": (3156, 3139), "assassin_ad": (3156, 3814), "fighter": (3156, 2504),
              "mage": (3102,), "assassin_ap": (3102,), "fighter_ap": (3102, 2504),
              "tank": (4401, 2504, 3065), "support_tank": (3190, 8020), "enchanter": (3190,)},
    "physical": {"marksman": (3026, 6673), "assassin_ad": (6333, 3026), "fighter": (6333, 3053),
                 "mage": (3157,), "assassin_ap": (3157,), "fighter_ap": (3157,),
                 "tank": (3143, 3075), "support_tank": (3109, 3190), "enchanter": (3190,)},
    "cc": {"marksman": (3139,), "assassin_ad": (3814,), "fighter": (3053,), "mage": (3102,),
           "assassin_ap": (3102,), "fighter_ap": (3102,), "tank": (3111,), "support_tank": (3222,),
           "enchanter": (3222,)},
    "crit": {"tank": (3143, 3110), "support_tank": (3110, 3143), "fighter": (3143, 6333),
             "fighter_ap": (3157,), "mage": (3157,), "assassin_ap": (3157,)},
    "armor": {"marksman": (3036, 3153), "assassin_ad": (6694,), "fighter": (3071, 6694, 3153),
              "mage": (3135, 6653), "assassin_ap": (3135,), "fighter_ap": (6653, 3135)},
    "shields": {"assassin_ad": (6695,), "fighter": (6695,)},
}
CORE: dict[str, tuple[int, ...]] = {
    "marksman": (3031, 6672, 3032, 3046, 3036, 3072), "mage": (6655, 4645, 3089, 3135, 3157),
    "assassin_ad": (3142, 6697, 3814, 6694, 6676), "assassin_ap": (4646, 4645, 3089, 3157),
    "fighter": (3071, 6610, 3053, 6333), "fighter_ap": (4633, 6653, 3157, 3089),
    "tank": (3068, 3084, 6665, 3075, 4401), "support_tank": (3190, 3109, 3050),
    "enchanter": (6617, 3107, 3222, 3504),
}
#: items fulfilling the same need (one is enough)
SAME_NEED: dict[str, frozenset[int]] = {
    "antiheal": frozenset({3033, 3123, 6609, 3165, 3916, 3075, 3076}),
    "cc": frozenset({3111, 3173, 3139, 3140, 3053, 3222}),
}
REASONS = {
    "antiheal": "{names} se soignent beaucoup (Blessures graves)",
    "magic": "{names} font surtout des dégâts magiques",
    "physical": "{names} font surtout des dégâts physiques",
    "cc": "beaucoup de contrôles en face ({names})",
    "crit": "{names} misent sur les coups critiques",
    "armor": "{names} sont très résistants",
    "shields": "{names} utilisent beaucoup de boucliers",
}


def _join(names: list[str]) -> str:
    names = [n for n in names if n][:2]
    return " et ".join(names) if names else "l'équipe adverse"


# ----------------------------------------------------------------------------- analysis
@dataclass
class EnemyProfile:
    physical: float = 0.0
    magic: float = 0.0
    true: float = 0.0
    needs: dict[str, float] = field(default_factory=dict)      # need -> severity
    names: dict[str, list[str]] = field(default_factory=dict)  # need -> champion names (most dangerous first)
    fed: list[str] = field(default_factory=list)


def _f(x: Any, d: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return d
    return v if math.isfinite(v) else d


def threat_weight(p: Any) -> float:
    """1 for an even player; more when fed (kills - deaths, item gold)."""
    k, d = _f(getattr(p, "kills", 0)), _f(getattr(p, "deaths", 0))
    return max(0.4, min(3.0, 1.0 + 0.15 * (k - d)))


def is_fed(p: Any) -> bool:
    return _f(getattr(p, "kills", 0)) >= 4 and _f(getattr(p, "kills", 0)) - _f(getattr(p, "deaths", 0)) >= 3


def enemy_profile(enemies: Iterable[Any], items: dict[int, Item] | None = None) -> EnemyProfile:
    items = items if items is not None else load_items()
    prof = EnemyProfile()
    acc: dict[str, list[tuple[float, str]]] = {}
    total = 0.0
    for p in enemies or ():
        alias = str(getattr(p, "champion_alias", "") or "")
        name = str(getattr(p, "champion_name", "") or alias)
        if not alias:
            continue
        w = threat_weight(p)
        tags = champion_tags(alias)
        ad, ap, tr = damage_split(alias)
        prof.physical += w * ad
        prof.magic += w * ap
        prof.true += w * tr
        total += w
        if is_fed(p):
            prof.fed.append(name)
        inv = [items.get(int(i)) for i in getattr(p, "items", None) or () if int(i) in items]

        def add(need: str, sev: float) -> None:
            acc.setdefault(need, []).append((sev, name))

        if alias in HEALERS:
            add("antiheal", w * (1.0 if alias in STRONG_HEALERS else 0.6))
        if any(it is not None and "LifeSteal" in it.tags and it.kind == "legendary" for it in inv):
            add("antiheal", 0.4 * w)
        meta = _meta(alias)
        if alias in HEAVY_CC or (meta is not None and meta.known and meta.ratings[2] >= 3
                                 and ("engage" in meta.style or "pick" in meta.style)):
            add("cc", 0.45)
        if alias in SHIELDS:
            add("shields", 0.45)
        crit = (("Marksman" in tags[:1] and alias not in NOT_CRIT) or alias in CRIT_EXTRA)
        crit_items = sum(1 for it in inv if it is not None and "CriticalStrike" in it.tags and it.kind == "legendary")
        if crit and (w >= 1.3 or crit_items >= 2):
            add("crit", w * 0.8)
        resist = sum(1 for it in inv if it is not None and ("Armor" in it.tags or "SpellBlock" in it.tags)
                     and it.kind == "legendary")
        tanky = tags[:1] == ("Tank",) or (meta is not None and meta.known and meta.ratings[1] >= 3)
        if (tanky and getattr(p, "level", 1) >= 9) or resist >= 2:
            add("armor", 0.6 + 0.3 * resist)
        if alias in BURST or tags[:1] == ("Assassin",) or (meta is not None and "burst" in meta.style):
            add("magic" if ap > ad else "physical", 0.5 * w if w >= 1.3 else 0.0)
    if total > 0:
        prof.physical, prof.magic, prof.true = (prof.physical / total, prof.magic / total, prof.true / total)
        if prof.magic >= 0.6:
            acc.setdefault("magic", []).append((1.0 + 2 * (prof.magic - 0.6), ""))
        if prof.physical >= 0.6:
            acc.setdefault("physical", []).append((1.0 + 2 * (prof.physical - 0.6), ""))
    for need, lst in acc.items():
        prof.needs[need] = round(sum(s for s, _ in lst), 3)
        prof.names[need] = [n for s, n in sorted(lst, key=lambda x: -x[0]) if n and s > 0]
    if "magic" in acc and not prof.names.get("magic"):
        prof.names["magic"] = []
    return prof


@dataclass(frozen=True)
class Recommendation:
    item_id: int
    item_name: str
    need: str                 # "antiheal" | "magic" | ... | "core"
    reason: str
    buy_now: tuple[int, ...] = ()
    buy_now_names: tuple[str, ...] = ()
    completes: bool = False
    gold: int = 0
    extras: tuple[int, ...] = ()            # cheap situational buys with the gold left (boots, control ward)
    extra_names: tuple[str, ...] = ()
    extra_why: str = ""                     # one line: why the extras

    @property
    def text(self) -> str:
        s = f"Prochain objet : {self.item_name}"
        if self.reason:
            s += f" — {self.reason}"
        return s + "."

    @property
    def buy_text(self) -> str | None:
        if not self.buy_now and not self.extras:
            return None
        tail = (" + " + " + ".join(self.extra_names)) if self.extra_names else ""
        if not self.buy_now:
            return f"Achète maintenant : {' + '.join(self.extra_names)} ({self.extra_why})." if self.extra_why \
                else f"Achète maintenant : {' + '.join(self.extra_names)}."
        if self.completes:
            return f"Achat immédiat : {self.item_name}{tail} ({self.gold} PO disponibles)."
        return f"Achat immédiat : {' + '.join(self.buy_now_names)}{tail}."


def _owned_need(need: str, owned: set[int]) -> bool:
    group = SAME_NEED.get(need)
    return bool(group and owned & group)


CONTROL_WARD = 2055
LEGENDARY_GOLD = 2200      # a completed legendary item (first-item rule)
BOOTS = 1001
#: tier-2 boots per need / class (enemy damage profile first, then the class default)
BOOTS_VS = {"physical": 3047, "magic": 3111, "cc": 3111}
BOOTS_CLASS = {"marksman": 3006, "mage": 3020, "assassin_ap": 3020, "enchanter": 3158, "assassin_ad": 3158,
               "support_tank": 3047, "tank": 3047, "fighter": 3047, "fighter_ap": 3020}
NO_BOOTS = frozenset({"Cassiopeia"})
TRINKETS = frozenset({3340, 3363, 3364, 3330, 3513})
BOOTS_GT = 420.0              # first boots from ~7:00 at the latest
BOOTS2_GT = 780.0             # tier-2 boots from ~13:00

#: starting items per start kind (``champ_select.start_kind``) and why (fallback of the builds file)
START_ITEMS: dict[str, tuple[int, ...]] = {
    "support": (3865, 2003, 2003), "jungle_tank": (1103, 2003), "jungle_mobile": (1102, 2003),
    "jungle": (1101, 2003), "marksman": (1055, 2003), "mage": (1056, 2003, 2003), "tank": (1054, 2003),
    "fighter": (1055, 2003),
}
START_WHY: dict[str, str] = {
    "support": "l'objet de support : or et balises", "jungle_tank": "familier résistant pour ta jungle",
    "jungle_mobile": "familier rapide pour ganker tôt", "jungle": "familier offensif pour nettoyer vite",
    "marksman": "dégâts et vol de vie en voie", "mage": "mana et puissance pour farmer de loin",
    "tank": "tenir la voie face aux échanges", "fighter": "dégâts et vol de vie en voie",
}
#: support quest done (Trésor des mondes, 3867): its free upgrade per class / subclass
BOUNTY_OF_WORLDS = 3867
SUPPORT_UPGRADE: dict[str, int] = {"support_tank": 3869, "catcher": 3876, "enchanter": 3870, "mage": 3871,
                                   "marksman": 3877, "assassin_ad": 3877, "fighter": 3877}
#: per-champion build exceptions: {"Kayle": {"class": "mage", "core": (3115, ...)}}
CHAMPION_BUILDS: dict[str, dict[str, Any]] = {}
#: header of the loaded builds file ({"schema", "version", "patch"}; {} = code fallback)
BUILDS_INFO: dict[str, Any] = {}
BUILDS_FILE = "item_builds.json"


def _ids_tuple(xs: Any) -> tuple[int, ...]:
    out = []
    for x in xs if isinstance(xs, (list, tuple)) else ():
        if isinstance(x, int) and not isinstance(x, bool) and x > 0:
            out.append(x)
    return tuple(out)


def apply_builds(data: Any) -> bool:
    """Replace the build tables with ``data`` (the ``assets/item_builds.json`` format); invalid
    parts keep the code defaults. Returns True when the file was usable. Never raises."""
    global CORE, NEED_ITEMS, SAME_NEED, BOOTS_CLASS, BOOTS_VS, START_ITEMS, START_WHY, SUPPORT_UPGRADE
    global CHAMPION_BUILDS, BUILDS_INFO
    try:
        if not isinstance(data, dict) or not isinstance(data.get("core"), dict):
            return False
        core = {c: _ids_tuple(v) for c, v in data["core"].items() if c in CLASSES and _ids_tuple(v)}
        if len(core) < len(CLASSES):
            return False
        need = {}
        for n, by_cls in (data.get("counters") or {}).items():
            if isinstance(by_cls, dict):
                need[str(n)] = {c: _ids_tuple(v) for c, v in by_cls.items() if c in CLASSES and _ids_tuple(v)}
        same = {str(n): frozenset(_ids_tuple(v)) for n, v in (data.get("same_need") or {}).items() if _ids_tuple(v)}
        boots = data.get("boots") if isinstance(data.get("boots"), dict) else {}
        bc = {c: int(i) for c, i in (boots.get("class") or {}).items() if c in CLASSES and isinstance(i, int)}
        bv = {n: int(i) for n, i in (boots.get("vs") or {}).items() if isinstance(i, int)}
        start = {k: _ids_tuple(v) for k, v in (data.get("start") or {}).items() if _ids_tuple(v)}
        why = {str(k): str(v) for k, v in (data.get("start_why") or {}).items() if isinstance(v, str)}
        upg = {str(k): int(i) for k, i in (data.get("support_upgrade") or {}).items() if isinstance(i, int)}
        champs: dict[str, dict[str, Any]] = {}
        for alias, row in (data.get("champions") or {}).items():
            if isinstance(row, dict):
                r: dict[str, Any] = {}
                if row.get("class") in CLASSES:
                    r["class"] = str(row["class"])
                if _ids_tuple(row.get("core")):
                    r["core"] = _ids_tuple(row.get("core"))
                if r:
                    champs[str(alias)] = r
        CORE = core
        NEED_ITEMS = need or NEED_ITEMS
        SAME_NEED = same or SAME_NEED
        BOOTS_CLASS = bc or BOOTS_CLASS
        BOOTS_VS = bv or BOOTS_VS
        START_ITEMS = start or START_ITEMS
        START_WHY = {**START_WHY, **why}
        SUPPORT_UPGRADE = upg or SUPPORT_UPGRADE
        CHAMPION_BUILDS = champs
        BUILDS_INFO = {k: data.get(k) for k in ("schema", "version", "patch", "generated") if data.get(k)}
        return True
    except Exception:
        log.warning("Invalid item builds table; code defaults kept", exc_info=True)
        return False


def _load_builds() -> None:
    try:
        from treeaicoach.paths import asset_path

        data = json.loads(asset_path(BUILDS_FILE).read_text(encoding="utf-8"))
    except Exception:
        log.warning("Item builds unavailable (assets/%s): code defaults", BUILDS_FILE, exc_info=True)
        return
    if not apply_builds(data):
        log.warning("Item builds file rejected (assets/%s): code defaults", BUILDS_FILE)


def core_items(alias: str, cls: str) -> tuple[int, ...]:
    """Core build path of a champion: its own (:data:`CHAMPION_BUILDS`) else its class."""
    own = (CHAMPION_BUILDS.get(str(alias or "")) or {}).get("core")
    return tuple(own) if own else CORE.get(cls, ())


def support_upgrade(alias: str, cls: str) -> int | None:
    """Free upgrade of the finished support item (Trésor des mondes) for this champion."""
    m = _meta(alias)
    sub = getattr(m, "subclass", "") if m is not None else ""
    lane = getattr(m, "lane_class", "") if m is not None else ""
    for key in (("catcher" if sub == "catcher" else ""), (lane if lane in ("mage", "marksman") else ""), cls):
        if key and key in SUPPORT_UPGRADE:
            return SUPPORT_UPGRADE[key]
    return None


def situational_buys(game: Any, cls: str, prof: EnemyProfile, owned: Iterable[int], gold_left: float,
                     items: dict[int, Item], objective_soon: bool = False) -> tuple[list[int], str]:
    """Cheap situational buys with the gold left after the build path (``(ids, why)``): a control
    ward when none is in the inventory (objective soon / support / jungle / mid game), boots in
    time, tier-2 boots against the enemy damage profile. Never contradicts the build path (it only
    spends leftover gold). Pure."""
    out: list[int] = []
    why: list[str] = []
    own = [int(i) for i in owned or ()]
    left = float(gold_left)
    me = getattr(game, "me", None)
    gt = _f(getattr(game, "game_time", 0.0))
    slots = len([i for i in own if i not in TRINKETS])
    alias = str(getattr(me, "champion_alias", "") or "")
    boots_owned = [i for i in own if i == BOOTS or (i in items and items[i].kind == "boots")]
    if alias not in NO_BOOTS and BOOTS in items:
        if not boots_owned and gt >= BOOTS_GT and left >= items[BOOTS].gold and slots < 6:
            out.append(BOOTS)
            left -= items[BOOTS].gold
            slots += 1
            why.append("des bottes pour te déplacer plus vite")
        elif boots_owned == [BOOTS] and gt >= BOOTS2_GT:
            need = max((n for n in ("physical", "magic", "cc") if prof.needs.get(n, 0.0) >= NEED_MIN),
                       key=lambda n: prof.needs.get(n, 0.0), default=None)
            iid = BOOTS_VS.get(need or "") or BOOTS_CLASS.get(cls)
            if iid in items:
                cost = remaining_cost(iid, own, items)
                if 0 < cost <= left:
                    out.append(iid)
                    left -= cost
                    why.append({"physical": "contre leurs dégâts physiques", "magic": "contre leurs dégâts magiques",
                                "cc": "moins de temps sous contrôle"}.get(need or "", "tes bottes complètes"))
    has_cw = CONTROL_WARD in own
    cw_role = str(getattr(me, "position", "") or "").upper() in ("UTILITY", "JUNGLE")
    if not has_cw and CONTROL_WARD in items and left >= items[CONTROL_WARD].gold and slots < 6 and gt >= 240.0 \
            and (objective_soon or cw_role or gt >= 900.0):
        out.append(CONTROL_WARD)
        why.append("une balise de contrôle pour l'objectif" if objective_soon else "une balise de contrôle")
    return out, " et ".join(why)


def recommend(game: Any, role: str | None = None, items: dict[int, Item] | None = None,
              gold: float | None = None, objective_soon: bool = False) -> Recommendation | None:
    """Best next item for me (None when spectating / unknown data). Never raises."""
    try:
        return _recommend(game, role, items, gold, objective_soon)
    except Exception:
        log.exception("itemization.recommend failed")
        return None


def _recommend(game: Any, role: str | None, items: dict[int, Item] | None, gold: float | None,
               objective_soon: bool = False) -> Recommendation | None:
    items = items if items is not None else load_items()
    me = getattr(game, "me", None)
    if me is None or not items:
        return None
    role = role or (getattr(me, "position", "") or None)
    cls = champion_class(me.champion_alias, role)
    owned_list = [int(i) for i in me.items or ()]
    owned = set(owned_list)
    prof = enemy_profile(getattr(game, "enemies", None) or (), items)
    gold = _f(getattr(game, "current_gold", 0.0) if gold is None else gold)

    def usable(iid: int) -> bool:
        it = items.get(iid)
        return it is not None and it.rift and iid not in owned and not any(
            iid in items[o].parts for o in owned if o in items)

    choice: tuple[int, str, str] | None = None
    # support quest done: the free upgrade of the support item first (it costs nothing)
    if BOUNTY_OF_WORLDS in owned:
        up = support_upgrade(me.champion_alias, cls)
        it = items.get(up) if up is not None else None
        if it is not None and it.rift and up not in owned:
            choice = (int(up), "support_upgrade", "ta quête de support est finie : amélioration gratuite")
    # V2 audit: a full counter item (Rappel mortel, Force de la nature...) as the FIRST item breaks the
    # build (no damage / no spike); before the first legendary only cheap counter components
    # (Appel du bourreau, Orbe de l'oubli...) may come before the core item
    first_done = any(o in items and items[o].gold >= LEGENDARY_GOLD and items[o].kind != "boots" for o in owned)
    for need, sev in sorted(prof.needs.items(), key=lambda kv: -kv[1]):
        if choice is not None:
            break
        if sev < NEED_MIN or _owned_need(need, owned):
            continue
        fed_need = any(n in prof.fed for n in (prof.names.get(need) or []))   # a fed assassin: rushing is right
        for iid in NEED_ITEMS.get(need, {}).get(cls, ()):
            if not first_done and not fed_need and iid in items and items[iid].gold >= LEGENDARY_GOLD \
                    and not any(o in items[iid].parts for o in owned):
                continue
            if usable(iid):
                names = prof.names.get(need) or []
                reason = REASONS[need].format(names=_join(names)) if names or need not in ("magic", "physical") else \
                    REASONS[need].format(names="Les ennemis").replace("Les ennemis font", "L'équipe adverse fait")
                fed = [n for n in names if n in prof.fed]
                if fed and need in ("magic", "physical"):
                    kills = sum(int(_f(getattr(p, "kills", 0))) for p in game.enemies
                                if (p.champion_name or p.champion_alias) in fed)
                    reason = f"{_join(fed)} {'ont' if len(fed) > 1 else 'a'} {kills} kills"
                choice = (iid, need, reason)
                break
        if choice:
            break
    if choice is None:
        # continue the item already started (owned component of a core item), else first core item
        core = [i for i in core_items(me.champion_alias, cls) if usable(i)]
        started = [i for i in core if any(o in items[i].parts for o in owned)]
        pick = (started or core or [None])[0]
        if pick is None:
            return None
        choice = (pick, "core", "")
    iid, need, reason = choice
    # boots are bought before a component once the game is a few minutes old (V2 audit: 700 gold at
    # 8:00 without boots went into a Cloak and the boots never fitted)
    reserve = 0.0
    gt = _f(getattr(game, "game_time", 0.0))
    has_boots = any(o == BOOTS or (o in items and items[o].kind == "boots") for o in owned_list)
    if not has_boots and BOOTS in items and gt >= BOOTS_GT and me.champion_alias not in NO_BOOTS \
            and gold >= items[BOOTS].gold:
        reserve = float(items[BOOTS].gold)
    buys, completes = plan_purchase(iid, owned_list, gold, items)
    if reserve and not completes:                  # completing a legendary beats the boots
        buys, completes = plan_purchase(iid, owned_list, gold - reserve, items)
    pool, spent = list(owned_list), 0
    for b in buys:
        p = list(pool)
        spent += _remaining(b, p, items)
        pool = p + [b]
    extras, why = situational_buys(game, cls, prof, pool, gold - spent, items, objective_soon)
    return Recommendation(iid, items[iid].name, need, reason, tuple(buys),
                          tuple(items[b].name for b in buys if b in items), completes, int(gold),
                          tuple(extras), tuple(items[e].name for e in extras if e in items), why)


# ----------------------------------------------------------------------------- advisor
@dataclass(frozen=True)
class BuyAdvice:
    text: str          # HUD / toast line ("Prochain objet : ... — ...")
    title: str         # toast title
    subtitle: str
    key: str
    moment: str        # "death" | "base" | "level" | "fed"
    rec: Recommendation
    t: float


class ItemAdvisor:
    """Decides when to show the build advice (text only by default). Thread-safe, never raises."""

    def __init__(self, items: dict[int, Item] | None = None) -> None:
        self._items = items
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self._was_dead: bool | None = None
            self._level: int | None = None
            self._items_owned: tuple[int, ...] | None = None
            self._fed: set[str] = set()
            self._last_emit = -math.inf
            self._shown: dict[tuple, float] = {}
            self._current: Recommendation | None = None

    def current(self) -> Recommendation | None:
        """Latest recommendation (for a persistent HUD line)."""
        return self._current

    def update(self, t: float, game: Any, role: str | None = None, in_base: bool | None = None,
               objective_soon: bool = False) -> list[BuyAdvice]:
        try:
            with self._lock:
                self._objective_soon = bool(objective_soon)
                return self._update(float(t), game, role, in_base)
        except Exception:
            log.exception("ItemAdvisor.update failed")
            return []

    def _update(self, t: float, game: Any, role: str | None, in_base: bool | None) -> list[BuyAdvice]:
        me = getattr(game, "me", None)
        if me is None:
            return []
        gt = _f(getattr(game, "game_time", 0.0))
        dead, level = bool(me.is_dead), int(me.level)
        inv = tuple(int(i) for i in me.items or ())
        fed = {str(p.champion_name or p.champion_alias) for p in game.enemies if is_fed(p)}
        moment = None
        if self._was_dead is not None:
            if dead and not self._was_dead:
                moment = "death"
            elif self._items_owned is not None and inv != self._items_owned and not dead:
                moment = "base"
            elif in_base and not dead:
                moment = "base"
            elif self._level is not None and level > self._level and any(self._level < s <= level for s in SPIKE_LEVELS):
                moment = "level"
            elif fed - self._fed:
                moment = "fed"
        self._was_dead, self._level, self._items_owned = dead, level, inv
        self._fed = fed
        rec = recommend(game, role, self._items, objective_soon=getattr(self, "_objective_soon", False))
        self._current = rec or self._current
        if moment is None or rec is None or gt < MIN_GT:
            return []
        gap = DEATH_GAP_S if moment == "death" else BASE_GAP_S if moment == "base" else MIN_GAP_S
        if t - self._last_emit < gap:
            return []
        shopping = moment in ("death", "base")
        sig = (rec.item_id, rec.need, rec.reason, (rec.buy_now + rec.extras) if shopping else ())
        last = self._shown.get(sig)
        if last is not None and t - last < REPEAT_S:
            return []
        same_item = [ts for s, ts in self._shown.items() if s[:2] == sig[:2]]
        if same_item and t - max(same_item) < REPEAT_S and not (shopping and (rec.buy_now or rec.extras)):
            return []
        self._shown[sig] = t
        self._last_emit = t
        sub = (rec.buy_text if shopping else None) or rec.reason or ""
        return [BuyAdvice(rec.text, f"ACHAT : {rec.item_name.upper()}"[:40], sub[:120],
                          f"item:{rec.item_id}:{rec.need}", moment, rec, t)]


_load_builds()

try:  # reload the tables after a runtime Data Dragon update
    from treeaicoach import game_data as _game_data

    _game_data.add_listener(_reset_tables)
except Exception:  # pragma: no cover - defensive
    log.debug("game_data listener not registered", exc_info=True)
