"""Audit every game data table of TreeAI Coach for completeness and consistency.

Run it after a patch (``python tools/validate_data.py``) or offline on the bundled data
(``--offline``, what ``tests/test_data_integrity.py`` does). It prints a findings table
(ERROR / WARN / INFO) and exits 1 when there is an ERROR.

Checks (``area`` column):

* ``reference`` - online: the latest Data Dragon version (fr_FR + en_US ``item.json`` /
  ``champion.json``) is the reference; offline: the bundled tables only (internal checks).
* ``items`` - bundled ``items.json``: same version / names (French and English) / prices /
  recipes / sold flag as the reference; no dead recipe link; every component of a sold item is
  sold (the advice never names an unbuyable part); prices add up; kinds; tier-3 boots.
* ``champions`` - every champion of the reference in the bundled index with an icon; French names.
* ``profiles`` - ``champion_meta.json`` (schema 2): one complete profile per champion with
  provenance per field, valid values (positions, lane class, ratings, curve...), same version
  as the item data; the curation file only names real champions.
* ``builds`` - ``item_builds.json``: real, sold items of the right kind; counters confirmed by
  the item data (flags / stats); champion overrides name real champions; ``itemization`` loaded it.
* ``advice`` - a grid of simulated shopping moments (classes x gold x inventories): the
  recommended item and the "achat immédiat" components are real, sold and affordable.
* ``matchups`` - ``matchups.json``: real champions / lane classes; every line is an instruction
  (verb first, ``presenter.CARD_VERBS``), "action : raison", 12 words at most with the name.
* ``objectives`` - ``objectives.json`` + ``objectives.OBJECTIVE_SCHEDULE`` against the official
  2026 rules (patch 26.1 notes + League of Legends Wiki, see :data:`OFFICIAL_2026`).
* ``atakhan`` - removed in 26.1: no data table / advice text may use it (legacy parsing of old
  game records is reported as INFO).
* ``wards`` - ``ward_spots.json``: every spot on a walkable pixel of the official minimap
  texture, the 12 Faelights at their game-file position, labels / roles / phases valid.
* ``code`` - champion / item tables written in other modules: unknown champion aliases, items
  not sold any more, boots lists missing tier-3 boots.

Usage::

    python tools/validate_data.py [--offline] [--root DIR] [--json out.json] [--quiet]
"""
from __future__ import annotations

import argparse
import ast
import importlib
import json
import math
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
NEW_ROOT = HERE.parent
if str(NEW_ROOT) not in sys.path:
    sys.path.insert(0, str(NEW_ROOT))

from tools import datalib  # noqa: E402

LEVELS = ("ERROR", "WARN", "INFO")
POSITIONS = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
LANE_CLASSES = ("juggernaut", "diver", "skirmisher", "tank", "assassin", "mage", "marksman", "enchanter", "engage")
PROFILE_FIELDS = ("name", "key", "tags", "cls", "class", "lane", "dmg", "rng", "ar", "hp", "hpl", "ms", "pos", "r",
                  "mob", "cc", "diff", "curve", "spike6", "wave", "split", "sus", "style", "g", "src")
STYLES = ("engage", "pick", "poke", "burst", "dive", "sustain", "peel", "splitpush")
MAX_WORDS = 12
#: Official Summoner's Rift objective rules, season 2026 (game seconds). Sources: patch 26.1 notes
#: (leagueoflegends.com/en-us/news/game-updates/patch-26-1-notes: Baron 20:00, Atakhan removed),
#: League of Legends Wiki (CC BY-SA 3.0) pages Dragon pit (drakes 5:00, +5:00, Elder after a team's
#: 4th drake, 6:00), Voidgrub camp (8:00, once, gone 14:45), Rift Herald (15:00, gone 19:45),
#: Baron Nashor (20:00, +6:00), Hand of Baron 180 s; patch history checked up to 26.19.
OFFICIAL_2026: dict[str, dict[str, float | None]] = {
    "dragon": {"first": 300, "respawn": 300, "soul": 4},
    "elder": {"respawn": 360},
    "grubs": {"first": 480, "respawn": None, "despawn": 885, "count": 3},
    "herald": {"first": 900, "despawn": 1185},
    "baron": {"first": 1200, "respawn": 360},
}
OFFICIAL_BUFFS = {"baron": 180.0, "elder": 150.0}
#: modules whose "atakhan" strings only parse old game records (post-game history)
ATAKHAN_LEGACY = {"analysis", "report", "replay", "ground_truth", "ai_advisor", "objectives"}
#: modules owned by the data pipeline: a dead reference there is an ERROR, elsewhere a WARN
DATA_MODULES = {"itemization", "meta", "game_plan", "champ_select", "wards", "objectives", "game_data"}
CODE_MODULES = ("itemization", "fight", "danger", "gank", "jungle_intel", "jungle_path", "macro", "tips", "coach",
                "coach_plus", "game_plan", "champ_select", "voice_policy", "positioning", "death_cause", "roles",
                "scoreboard", "praise", "plays", "spikes", "phase", "tactics", "reminders", "ai_advisor", "analysis",
                "game_changers")


@dataclass
class Finding:
    level: str
    area: str
    check: str
    detail: str


class Audit:
    def __init__(self, root: Path, offline: bool, reference: dict | None, log: Callable[[str], None]):
        self.root = root
        self.assets = root / "treeaicoach" / "assets"
        self.offline = offline
        self.ref = reference
        self.log = log
        self.findings: list[Finding] = []
        self.items: dict[str, dict] = {}
        self.aliases: set[str] = set()
        self.names_fr: dict[str, str] = {}

    def add(self, level: str, area: str, check: str, detail: str) -> None:
        self.findings.append(Finding(level, area, check, detail))

    def asset(self, name: str) -> Any:
        p = self.assets / name
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:   # noqa: BLE001
            self.add("ERROR", "files", "json", f"{name}: unreadable ({exc})")
            return None


def _short(xs: Any, n: int = 8) -> str:
    xs = list(xs)
    return ", ".join(str(x) for x in xs[:n]) + (f" (+{len(xs) - n})" if len(xs) > n else "")


# ----------------------------------------------------------------------------- reference
def load_reference(offline: bool, log: Callable[[str], None] = print) -> dict | None:
    """Latest Data Dragon data (online) or None (offline)."""
    if offline:
        return None
    try:
        version = datalib.latest_version(False)
        ref = {"version": version,
               "items_fr": datalib.ddragon(version, "item", "fr_FR"), "items_en": datalib.ddragon(version, "item", "en_US"),
               "champs_fr": datalib.ddragon(version, "champion", "fr_FR"),
               "champs_en": datalib.ddragon(version, "champion", "en_US")}
        try:
            ref["shop"] = datalib.classic_shop(datalib.get_json(datalib.MAP11_BIN, max_age_s=86400.0))
        except Exception as exc:   # noqa: BLE001
            log(f"  CLASSIC shop list unavailable ({type(exc).__name__})")
        try:
            ref["geo"] = datalib.get_json(datalib.MAP11_PLACEABLES, max_age_s=86400.0)
        except Exception as exc:   # noqa: BLE001
            log(f"  map placeables unavailable ({type(exc).__name__})")
        return ref
    except Exception as exc:   # noqa: BLE001
        log(f"  reference unavailable ({type(exc).__name__}: {exc})")
        return {"error": f"{type(exc).__name__}: {exc}"}


# ----------------------------------------------------------------------------- items
def check_items(a: Audit) -> None:
    data = a.asset("items.json")
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        a.add("ERROR", "items", "file", "assets/items.json missing or invalid")
        return
    items: dict[str, dict] = data["items"]
    a.items = items
    sold = {k: v for k, v in items.items() if v.get("p")}
    a.add("INFO", "items", "table", f"Data Dragon {data.get('version')}: {len(items)} items, {len(sold)} sold on the "
          f"Rift, {len(data.get('not_sr') or [])} excluded by the CLASSIC shop filter")
    if "not_sr" not in data:
        a.add("WARN", "items", "shop-filter", "no CLASSIC shop filter (not_sr): Data Dragon flags Swiftplay / ARAM "
              "starters (Guardian's items, duplicated jungle pets) as sold on the Rift")
    no_flags = "x" not in next(iter(items.values()), {})
    if all("x" not in v for v in items.values()):
        a.add("WARN", "items", "flags", "no semantic flags (x): counters cannot be checked against the item data")
    dead = sorted({f"{k}->{p}" for k, v in items.items() for p in list(v.get("f") or []) + list(v.get("i") or [])
                   if str(p) not in items and int(p) < 10000})
    if dead:
        a.add("WARN", "items", "recipe-links", f"{len(dead)} recipe links to unknown ids: {_short(dead)}")
    unsold_parts = sorted({f"{v.get('n')} <- {items[str(p)].get('n')}" for k, v in sold.items()
                           for p in v.get("f") or () if str(p) in items and not items[str(p)].get("p")
                           and v.get("k") not in ("starter",) and int(v.get("b") or 0) > 0})
    if unsold_parts:
        a.add("ERROR", "items", "unsold-component", f"sold items built from unsold parts: {_short(unsold_parts)}")
    bad_price = []
    for k, v in sold.items():
        parts = [items.get(str(p)) for p in v.get("f") or ()]
        if parts and all(isinstance(p, dict) for p in parts):
            if int(v.get("g") or 0) != int(v.get("b") or 0) + sum(int(p.get("g") or 0) for p in parts):
                bad_price.append(f"{v.get('n')} {v.get('g')}")
    if bad_price:
        a.add("WARN", "items", "price-sum", f"total != combine cost + parts: {_short(bad_price)}")
    tier3 = [v.get("n") for v in sold.values() if any((items.get(str(p)) or {}).get("k") == "boots"
                                                      for p in v.get("f") or ()) and v.get("k") != "boots"]
    if tier3:
        a.add("ERROR", "items", "boots-kind", f"tier-3 boots not classified as boots: {_short(tier3)}")
    no_fr = [k for k, v in sold.items() if not str(v.get("n") or "").strip() or "<" in str(v.get("n"))]
    if no_fr:
        a.add("ERROR", "items", "names", f"{len(no_fr)} sold items without a clean French name: {_short(no_fr)}")
    unnamed = [k for k, v in items.items() if not v.get("p") and not str(v.get("n") or "").strip()]
    if unnamed:
        a.add("INFO", "items", "names", f"{len(unnamed)} unsold placeholder ids without a name: {_short(unnamed)}")
    ref = a.ref
    if not ref or "error" in ref:
        return
    if datalib.version_tuple(data.get("version")) < datalib.version_tuple(ref["version"]):
        a.add("ERROR", "items", "version", f"items.json is Data Dragon {data.get('version')}, live is {ref['version']}")
    from treeaicoach.game_data import build_items_table
    try:
        rebuilt = build_items_table(ref["items_fr"], ref["items_en"], ref["version"], shop=ref.get("shop"))["items"]
    except TypeError:                     # older game_data (no shop filter): apply it here
        rebuilt = build_items_table(ref["items_fr"], ref["items_en"], ref["version"])["items"]
        for k, row in rebuilt.items():
            if ref.get("shop") and row.get("p") and int(k) not in ref["shop"]:
                row["p"] = 0
    missing = sorted(set(rebuilt) - set(items), key=int)
    removed = sorted(set(items) - set(rebuilt), key=int)
    if missing:
        a.add("ERROR", "items", "new-items", f"{len(missing)} live items missing: "
              f"{_short(rebuilt[k]['n'] for k in missing)}")
    if removed:
        a.add("ERROR", "items", "removed-items", f"{len(removed)} items no longer in Data Dragon: "
              f"{_short(items[k].get('n') for k in removed)}")
    diffs: dict[str, list[str]] = {}
    for k, r in rebuilt.items():
        b = items.get(k)
        if not b:
            continue
        for field, label in (("n", "fr-name"), ("en", "en-name"), ("g", "price"), ("f", "recipe"), ("p", "sold"),
                             ("k", "kind")):
            if b.get(field) != r.get(field):
                diffs.setdefault(label, []).append(f"{r.get('en')}: {b.get(field)!r} -> {r.get(field)!r}")
    for label, lst in diffs.items():
        a.add("ERROR", "items", label, f"{len(lst)} differences with Data Dragon {ref['version']}: {_short(lst, 5)}")
    if not missing and not removed and not diffs:
        a.add("INFO", "items", "reference", f"identical to Data Dragon {ref['version']} (fr_FR + en_US)"
              + (" + CLASSIC shop" if ref.get("shop") else ""))
    _ = no_flags


# ----------------------------------------------------------------------------- champions
def check_champions(a: Audit) -> None:
    idx = a.asset("icons/champions/index.json")
    champs = (idx or {}).get("champions") if isinstance(idx, dict) else None
    if not isinstance(champs, list) or not champs:
        a.add("ERROR", "champions", "index", "assets/icons/champions/index.json missing or empty")
        return
    a.aliases = {str(c.get("alias")) for c in champs}
    a.names_fr = {str(c.get("alias")): str(c.get("name_fr") or "") for c in champs}
    no_icon = [c.get("alias") for c in champs if not (a.assets / "icons" / "champions" / str(c.get("icon") or "")).is_file()]
    if no_icon:
        a.add("ERROR", "champions", "icons", f"no bundled icon: {_short(no_icon)}")
    a.add("INFO", "champions", "index", f"{len(champs)} champions (Data Dragon {idx.get('patch')})")
    ref = a.ref
    if not ref or "error" in ref:
        return
    live = set(ref["champs_en"])
    missing = sorted(live - a.aliases)
    gone = sorted(a.aliases - live)
    if missing:
        a.add("ERROR", "champions", "new-champions", f"missing from the bundled index: {_short(missing)} (the runtime "
              "refresh adds them, without a profile until tools/fetch_all.py runs)")
    if gone:
        a.add("ERROR", "champions", "removed", f"not in Data Dragon {ref['version']}: {_short(gone)}")
    bad_fr = [f"{al}: {a.names_fr.get(al)!r} -> {c.get('name')!r}" for al, c in ref["champs_fr"].items()
              if al in a.names_fr and a.names_fr[al] != c.get("name")]
    if bad_fr:
        a.add("ERROR", "champions", "fr-names", f"French names differ: {_short(bad_fr)}")
    if not missing and not gone and not bad_fr:
        a.add("INFO", "champions", "reference", f"every live champion ({len(live)}) present with its French name")


# ----------------------------------------------------------------------------- profiles
def check_profiles(a: Audit) -> None:
    data = a.asset("champion_meta.json")
    champs = (data or {}).get("champions") if isinstance(data, dict) else None
    if not isinstance(champs, dict):
        a.add("ERROR", "profiles", "file", "assets/champion_meta.json missing or invalid")
        return
    schema = data.get("schema")
    if schema != 2:
        a.add("ERROR", "profiles", "schema", f"schema {schema!r}: no provenance per field, no level-6 spike / "
              "waveclear / splitpush / sustain / lane class (schema 2 expected)")
    missing = sorted(a.aliases - set(champs)) if a.aliases else []
    if missing:
        a.add("ERROR", "profiles", "coverage", f"champions without a profile: {_short(missing)}")
    extra = sorted(set(champs) - a.aliases) if a.aliases else []
    if extra:
        a.add("ERROR", "profiles", "dead", f"profiles of unknown champions: {_short(extra)}")
    incomplete: list[str] = []
    invalid: list[str] = []
    no_src: list[str] = []
    empty_pos: list[str] = []
    for alias, p in sorted(champs.items()):
        miss = [f for f in PROFILE_FIELDS if f not in p]
        if miss:
            incomplete.append(f"{alias} ({','.join(miss[:4])}{'...' if len(miss) > 4 else ''})")
            if schema != 2:
                continue
        if not p.get("pos"):
            empty_pos.append(alias)
        errs = []
        if p.get("dmg") not in ("P", "M", "X"):
            errs.append("dmg")
        if p.get("rng") not in ("M", "R"):
            errs.append("rng")
        if any(x not in POSITIONS for x in p.get("pos") or ()):
            errs.append("pos")
        if p.get("curve") not in ("early", "mid", "late"):
            errs.append("curve")
        r = p.get("r") or []
        if len(r) != 5 or any(not isinstance(x, int) or not 0 <= x <= 3 for x in r):
            errs.append("r")
        if schema == 2:
            if p.get("lane") not in LANE_CLASSES:
                errs.append("lane")
            for f in ("spike6", "wave", "split", "sus", "mob", "diff"):
                if not isinstance(p.get(f), int) or not 1 <= p[f] <= 3:
                    errs.append(f)
            if any(s not in STYLES for s in p.get("style") or ()):
                errs.append("style")
            if p.get("g") not in ("m", "f"):
                errs.append("g")
            src = p.get("src") or {}
            if any(f not in src for f in PROFILE_FIELDS if f != "src"):
                no_src.append(alias)
        if a.names_fr.get(alias) and p.get("name") and p.get("name") != a.names_fr[alias]:
            errs.append(f"name {p.get('name')!r} != index {a.names_fr[alias]!r}")
        if errs:
            invalid.append(f"{alias}: {','.join(errs)}")
    if incomplete:
        a.add("ERROR", "profiles", "fields", f"{len(incomplete)} incomplete profiles: {_short(incomplete, 6)}")
    if empty_pos:
        a.add("ERROR", "profiles", "positions", f"{len(empty_pos)} champions without a position: {_short(empty_pos)}")
    if invalid:
        a.add("ERROR", "profiles", "values", f"{len(invalid)} invalid profiles: {_short(invalid, 6)}")
    if no_src:
        a.add("ERROR", "profiles", "provenance", f"{len(no_src)} profiles without a source per field: {_short(no_src)}")
    if schema == 2 and not (missing or incomplete or invalid or no_src):
        srcs: dict[str, int] = {}
        for p in champs.values():
            for s in (p.get("src") or {}).values():
                srcs[s] = srcs.get(s, 0) + 1
        a.add("INFO", "profiles", "complete", f"{len(champs)} complete profiles (patch {data.get('patch')}), "
              f"fields by source {dict(sorted(srcs.items(), key=lambda kv: -kv[1]))}")
    items_v = str((a.asset("items.json") or {}).get("version") or "")
    if str(data.get("version") or "") != items_v:
        a.add("WARN", "profiles", "version", f"profiles {data.get('version')} vs items {items_v}")
    if a.ref and "error" not in a.ref and str(data.get("version")) != a.ref["version"]:
        a.add("WARN", "profiles", "live", f"profiles built from {data.get('version')}, live is {a.ref['version']}")
    # curation file (tools/data/champion_curation.json) of the NEW pipeline
    cur_path = a.root / "tools" / "data" / "champion_curation.json"
    if cur_path.is_file() and a.aliases:
        cur = json.loads(cur_path.read_text(encoding="utf-8"))
        names: set[str] = set()

        def walk(o: Any) -> None:
            if isinstance(o, dict):
                for k, v in o.items():
                    if not k.startswith("_") and k[:1].isupper():
                        names.add(k)
                    walk(v)
            elif isinstance(o, list):
                for x in o:
                    if isinstance(x, str) and x[:1].isupper() and " " not in x:
                        names.add(x)
                    else:
                        walk(x)
        walk({k: v for k, v in cur.items() if not k.startswith("_")})
        dead = sorted(n for n in names if n not in a.aliases and n.upper() != n)
        if dead:
            a.add("ERROR", "profiles", "curation-dead", f"curation names unknown champions: {_short(dead)}")


# ----------------------------------------------------------------------------- builds
NEED_CHECK = {"antiheal": ({"antiheal"}, set()), "magic": ({"lifeline", "spellshield", "cleanse"}, {"SpellBlock"}),
              "physical": ({"stasis", "revive", "lifeline"}, {"Armor"}), "cc": ({"cleanse", "spellshield"}, {"Tenacity"}),
              "crit": ({"anticrit", "antiattack", "stasis"}, {"Armor"}),
              "armor": ({"armorpen", "magicpen", "armorshred", "pcthp"}, set()), "shields": ({"shieldbreak"}, set())}


def _item_ok(a: Audit, iid: Any, kinds: tuple[str, ...]) -> str | None:
    it = a.items.get(str(iid))
    if it is None:
        return f"{iid} unknown"
    if not it.get("p"):
        return f"{it.get('n')} ({iid}) not sold"
    if kinds and it.get("k") not in kinds:
        return f"{it.get('n')} ({iid}) is {it.get('k')}"
    return None


def check_builds(a: Audit) -> None:
    data = a.asset("item_builds.json")
    if not isinstance(data, dict):
        a.add("ERROR", "builds", "file", "assets/item_builds.json missing: build paths, counters, boots and starting "
              "items are hard-coded in itemization.py / champ_select.py without provenance or data checks")
        _check_builds_code(a)
        return
    probs: list[str] = []
    for cls, ids in (data.get("core") or {}).items():
        probs += [f"core.{cls}: {e}" for i in ids if (e := _item_ok(a, i, ("legendary",)))]
    unconfirmed = []
    for need, by_cls in (data.get("counters") or {}).items():
        flags, tags = NEED_CHECK.get(need, (set(), set()))
        for cls, ids in by_cls.items():
            for i in ids:
                e = _item_ok(a, i, ("legendary", "component", "boots"))
                if e:
                    probs.append(f"counters.{need}.{cls}: {e}")
                    continue
                it = a.items[str(i)]
                if (flags or tags) and not (set(it.get("x") or ()) & flags or set(it.get("t") or ()) & tags):
                    unconfirmed.append(f"{need}.{cls}: {it.get('n')}")
    for cls, i in ((data.get("boots") or {}).get("class") or {}).items():
        if e := _item_ok(a, i, ("boots",)):
            probs.append(f"boots.{cls}: {e}")
    for n, i in ((data.get("boots") or {}).get("vs") or {}).items():
        if e := _item_ok(a, i, ("boots",)):
            probs.append(f"boots.vs.{n}: {e}")
    for kind, ids in (data.get("start") or {}).items():
        probs += [f"start.{kind}: {e}" for i in ids if (e := _item_ok(a, i, ("starter", "consumable")))]
    for k, i in (data.get("support_upgrade") or {}).items():
        it = a.items.get(str(i)) or {}
        if 3867 not in (it.get("f") or []):
            probs.append(f"support_upgrade.{k}: {i} is not an upgrade of Trésor des mondes")
    dead = [al for al in (data.get("champions") or {}) if a.aliases and al not in a.aliases]
    if dead:
        probs.append(f"champion overrides of unknown champions: {_short(dead)}")
    for al, row in (data.get("champions") or {}).items():
        probs += [f"champions.{al}: {e}" for i in row.get("core") or () if (e := _item_ok(a, i, ("legendary",)))]
    if probs:
        a.add("ERROR", "builds", "items", f"{len(probs)} invalid entries: {_short(probs, 6)}")
    if unconfirmed:
        a.add("ERROR", "builds", "counters", f"counters not confirmed by the item data: {_short(unconfirmed, 6)}")
    if data.get("problems"):
        a.add("WARN", "builds", "rules", f"rule items rejected by the generator: {_short(data['problems'], 4)}")
    if not probs and not unconfirmed:
        n_lists = sum(len(v) for v in (data.get("counters") or {}).values())
        a.add("INFO", "builds", "complete", f"patch {data.get('patch')}: {len(data.get('core') or {})} core paths, "
              f"{n_lists} counter lists, {len(data.get('start') or {})} starts, "
              f"{len(data.get('support_upgrade') or {})} support upgrades, {len(data.get('champions') or {})} "
              "champion overrides - all real, sold, of the right kind and confirmed by the item data")
    _check_builds_code(a, data)


def _check_builds_code(a: Audit, data: dict | None = None) -> None:
    try:
        iz = importlib.import_module("treeaicoach.itemization")
    except Exception as exc:   # noqa: BLE001
        a.add("ERROR", "builds", "import", f"itemization: {exc}")
        return
    if data is not None and not getattr(iz, "BUILDS_INFO", None):
        a.add("ERROR", "builds", "loaded", "itemization did not load assets/item_builds.json (code defaults in use)")
    probs = []
    for cls, ids in getattr(iz, "CORE", {}).items():
        probs += [f"CORE.{cls}: {e}" for i in ids if (e := _item_ok(a, i, ("legendary",)))]
    for need, by in getattr(iz, "NEED_ITEMS", {}).items():
        for cls, ids in by.items():
            probs += [f"NEED_ITEMS.{need}.{cls}: {e}" for i in ids if (e := _item_ok(a, i, ()))]
    if probs:
        a.add("ERROR", "builds", "itemization", f"tables in use name bad items: {_short(probs, 6)}")


# ----------------------------------------------------------------------------- advice simulation
SIM_CHAMPS = ("Jinx", "Ahri", "Zed", "Akali", "Garen", "Mordekaiser", "Malphite", "Leona", "Janna", "Kayle", "KogMaw",
              "Ezreal", "Senna", "Locke", "Zaahen", "Thresh", "Lux", "Darius", "Vayne", "Gwen")
SIM_GOLD = (0, 150, 300, 450, 875, 1100, 1300, 2200, 3400)


def check_advice(a: Audit) -> None:
    try:
        iz = importlib.import_module("treeaicoach.itemization")
        lc = importlib.import_module("treeaicoach.live_client")
    except Exception as exc:   # noqa: BLE001
        a.add("ERROR", "advice", "import", str(exc))
        return
    items = iz.load_items()
    enemies = [lc.PlayerInfo(champion_alias=al, champion_name=al, team="CHAOS", level=9,
                             scores={"kills": k, "deaths": 0, "assists": 0, "creepScore": 0, "wardScore": 0})
               for al, k in (("Soraka", 0), ("Aatrox", 5), ("Zed", 4), ("Caitlyn", 1), ("Leona", 0))]
    bad: list[str] = []
    n = 0
    for alias in SIM_CHAMPS:
        if a.aliases and alias not in a.aliases:
            continue
        for role in ("TOP", "MIDDLE", "BOTTOM", "UTILITY", "JUNGLE"):
            cls = iz.champion_class(alias, role)
            core = list(getattr(iz, "core_items", lambda _a, c: iz.CORE.get(c, ()))(alias, cls))
            first = core[0] if core else None
            invs: list[list[int]] = [[], [1055, 2003], [1001, 1036]]
            if first in items and items[first].parts:
                invs.append([items[first].parts[0], 1001])
            if first is not None:
                invs.append([first, 1001])
            if role == "UTILITY":
                invs.append([3867, 1001])
            for inv in invs:
                for gold in SIM_GOLD:
                    for gt in (600.0, 1500.0):
                        me = lc.PlayerInfo(champion_alias=alias, champion_name=alias, team="ORDER", items=list(inv),
                                           level=9, position=role)
                        game = lc.GameInfo(game_time=gt, me=me, enemies=enemies, current_gold=float(gold))
                        rec = iz.recommend(game, role)
                        n += 1
                        if rec is None:
                            continue
                        it = items.get(rec.item_id)
                        if it is None or not it.rift:
                            bad.append(f"{alias}/{role}: target {rec.item_id} not sold")
                        if rec.item_name != (it.name if it else None):
                            bad.append(f"{alias}/{role}: name {rec.item_name!r}")
                        pool, cost = list(inv), 0
                        for b in tuple(rec.buy_now) + tuple(rec.extras):
                            bi = items.get(b)
                            if bi is None or not bi.rift:
                                bad.append(f"{alias}/{role}: buys unsold {b}")
                                continue
                            p = list(pool)
                            cost += iz._remaining(b, p, items)
                            pool = p + [b]
                        if cost > gold:
                            bad.append(f"{alias}/{role} {gold} PO: plan costs {cost}")
                        if rec.completes and rec.item_id not in rec.buy_now:
                            bad.append(f"{alias}/{role}: completes without buying the item")
    if bad:
        a.add("ERROR", "advice", "shopping", f"{len(bad)} bad shopping advices in {n} simulated moments: "
              f"{_short(sorted(set(bad)), 6)}")
    else:
        a.add("INFO", "advice", "shopping", f"{n} simulated shopping moments ({len(SIM_CHAMPS)} champions x roles x "
              "gold x inventories): every named item real and sold, every 'achat immédiat' affordable")


# ----------------------------------------------------------------------------- matchups
def _verbs() -> frozenset[str]:
    try:
        return importlib.import_module("treeaicoach.presenter").CARD_VERBS
    except Exception:   # noqa: BLE001
        return frozenset()


def _line_problem(line: str, name: str, verbs: frozenset[str], female: bool = False) -> str | None:
    t = line.replace("{opp}", name).replace("{il}", "elle" if female else "il").replace("{e}", "e" if female else "")
    if re.search(r"\{\w+\}", t):
        return "unknown placeholder"
    if " : " not in t:
        return 'no " : "'
    if len(t.split()) > MAX_WORDS:
        return f"{len(t.split())} words"
    first = re.split(r"[\s:,!.']", t.strip(), maxsplit=1)[0].lower()
    if verbs and first not in verbs:
        return f"starts with {first!r} (not an instruction verb)"
    return None


def check_matchups(a: Audit) -> None:
    data = a.asset("matchups.json")
    if not isinstance(data, dict):
        a.add("ERROR", "matchups", "file", "assets/matchups.json missing: lane plans only come from 5 generic profile "
              "rules (curve, range, mobility, poke)")
        return
    verbs = _verbs()
    profiles = ((a.asset("champion_meta.json") or {}).get("champions") or {})
    female = {al for al, p in profiles.items() if isinstance(p, dict) and p.get("g") == "f"}
    longest = "Renata Glasc"
    probs: list[str] = []
    for cls, lines in (data.get("vs_class") or {}).items():
        if cls not in LANE_CLASSES:
            probs.append(f"vs_class.{cls}: unknown lane class")
        for line in lines:
            for fem in (False, True):
                if (e := _line_problem(line, longest, verbs, fem)):
                    probs.append(f"vs_class.{cls}: {e}: {line}")
                    break
    for key, lines in (data.get("pair_class") or {}).items():
        mine, _sep, theirs = key.partition(">")
        if mine not in LANE_CLASSES or theirs not in LANE_CLASSES:
            probs.append(f"pair_class.{key}: unknown lane class")
        for line in lines:
            if (e := _line_problem(line, longest, verbs)):
                probs.append(f"pair_class.{key}: {e}: {line}")
    for al, lines in (data.get("vs_champion") or {}).items():
        if a.aliases and al not in a.aliases:
            probs.append(f"vs_champion.{al}: unknown champion")
        for line in lines:
            if (e := _line_problem(line, a.names_fr.get(al, al), verbs, al in female)):
                probs.append(f"vs_champion.{al}: {e}: {line}")
    for key, lines in (data.get("pair_champion") or {}).items():
        mine, _sep, theirs = key.partition(">")
        if a.aliases and (mine not in a.aliases or theirs not in a.aliases):
            probs.append(f"pair_champion.{key}: unknown champion")
        for line in lines:
            if (e := _line_problem(line, a.names_fr.get(theirs, theirs), verbs, theirs in female)):
                probs.append(f"pair_champion.{key}: {e}: {line}")
    short_cls = [c for c in LANE_CLASSES if len((data.get("vs_class") or {}).get(c) or []) < 2]
    if short_cls:
        probs.append(f"lane classes with < 2 tips: {short_cls}")
    if probs:
        a.add("ERROR", "matchups", "lines", f"{len(probs)} problems: {_short(probs, 6)}")
    tops = sorted(al for al, p in profiles.items() if isinstance(p, dict) and (p.get("pos") or [""])[0] == "TOP")
    covered = [al for al in tops if al in (data.get("vs_champion") or {})]
    n_lines = sum(len(v) for k in ("vs_class", "pair_class", "vs_champion", "pair_champion")
                  for v in (data.get(k) or {}).values())
    a.add("INFO", "matchups", "coverage", f"{n_lines} tips: {len(data.get('vs_class') or {})} lane classes, "
          f"{len(data.get('pair_class') or {})} class pairs, {len(data.get('vs_champion') or {})} champions, "
          f"{len(data.get('pair_champion') or {})} champion pairs; top laners covered by name: "
          f"{len(covered)}/{len(tops)}" + (f" (missing {_short(sorted(set(tops) - set(covered)), 6)})"
                                           if len(covered) < len(tops) else ""))


# ----------------------------------------------------------------------------- objectives
def check_objectives(a: Audit) -> None:
    data = a.asset("objectives.json")
    if not isinstance(data, dict):
        a.add("ERROR", "objectives", "file", "assets/objectives.json missing")
        data = {}
    try:
        obj = importlib.import_module("treeaicoach.objectives")
        code = obj.OBJECTIVE_SCHEDULE
        merged = obj.load_schedule([a.assets / "objectives.json"])
        buffs = getattr(obj, "BUFF_DURATION_S", {})
        legacy = getattr(obj, "LEGACY_KEYS", frozenset())
        kill_events = getattr(obj, "KILL_EVENTS", {})
    except Exception as exc:   # noqa: BLE001
        a.add("ERROR", "objectives", "import", str(exc))
        return
    diffs = []
    for src_name, table in (("objectives.json", data), ("OBJECTIVE_SCHEDULE", code), ("schedule in use", merged)):
        for key, ref in OFFICIAL_2026.items():
            got = table.get(key) if isinstance(table, dict) else None
            if not isinstance(got, dict):
                diffs.append(f"{src_name}: {key} missing")
                continue
            for f, v in ref.items():
                g = got.get(f)
                if (v is None and g is not None) or (v is not None and (g is None or float(g) != float(v))):
                    diffs.append(f"{src_name}: {key}.{f} = {g} (official {v})")
    for k, v in OFFICIAL_BUFFS.items():
        if float(buffs.get(k) or 0) != v:
            diffs.append(f"BUFF_DURATION_S.{k} = {buffs.get(k)} (official {v})")
    if diffs:
        a.add("ERROR", "objectives", "timers", f"{len(diffs)} timers differ from the 2026 rules: {_short(diffs, 5)}")
    else:
        a.add("INFO", "objectives", "timers", "dragon 5:00 (+5:00, soul 4), elder +6:00, voidgrubs 8:00-14:45 (x3, "
              "once), herald 15:00-19:45, baron 20:00 (+6:00), buffs 3:00 / 2:30: match the 2026 rules")
    if any("atakhan" in str(k).lower() for k in list(data) + list(code) + list(merged)) or \
            any("atakhan" in str(k).lower() or "atakhan" in str(v).lower() for k, v in kill_events.items()):
        a.add("ERROR", "atakhan", "objectives", "an Atakhan objective is still scheduled / tracked")
    if "atakhan" not in legacy:
        a.add("WARN", "atakhan", "legacy-keys", "old override files with an 'atakhan' key are not explicitly ignored")


def check_atakhan(a: Audit) -> None:
    pkg = a.root / "treeaicoach"
    hits_err, hits_info = [], []
    for name in sorted(p.name for p in a.assets.glob("*.json")):
        if name in ("manifest.json",):
            continue
        data = a.asset(name)
        text = json.dumps({k: v for k, v in data.items() if not str(k).startswith("_")} if isinstance(data, dict)
                          else data, ensure_ascii=False).lower()
        if "atakhan" in text:
            hits_err.append(f"assets/{name}")
    for path in sorted(pkg.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body
                and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs \
                    and "atakhan" in node.value.lower():
                where = f"{path.stem}:{getattr(node, 'lineno', '?')}"
                if path.stem in ATAKHAN_LEGACY:
                    hits_info.append(where)
                else:
                    hits_err.append(where)
    if hits_err:
        a.add("ERROR", "atakhan", "uses", f"Atakhan (removed in 26.1) used in: {_short(hits_err)}")
    else:
        a.add("INFO", "atakhan", "uses", "no data table / advice text uses Atakhan")
    if hits_info:
        a.add("INFO", "atakhan", "legacy", f"{len(hits_info)} strings parse old game records "
              f"(AtakhanKill events of pre-26.1 games): {_short(hits_info, 6)}")


# ----------------------------------------------------------------------------- wards
def _walk_mask(a: Audit):
    try:
        import cv2
        import numpy as np
    except Exception:   # noqa: BLE001
        return None
    tex = cv2.imread(str(a.assets / "minimap" / "2dlevelminimap_base_baron1.png"))
    if tex is None:
        return None
    return cv2.erode((tex.max(axis=2) > 40).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0


def check_wards(a: Audit) -> None:
    data = a.asset("ward_spots.json")
    try:
        wards = importlib.import_module("treeaicoach.wards")
    except Exception as exc:   # noqa: BLE001
        a.add("ERROR", "wards", "import", str(exc))
        return
    spots = list(getattr(wards, "SPOTS", ()))
    if not isinstance(data, dict):
        a.add("ERROR", "wards", "file", "assets/ward_spots.json missing: spots hard-coded in wards.py, Faelights "
              "placed by hand (approximate), no time / phase relevance")
    mask = _walk_mask(a)
    probs = []
    if mask is not None:
        for s in spots:
            for team in ("ORDER", "CHAOS"):
                u, v = s.uv_for(team)
                if not mask[int(v * 512), int(u * 512)]:
                    probs.append(f"{s.id}/{team} ({u:.3f}, {v:.3f}) not walkable")
    else:
        a.add("WARN", "wards", "walkable", "texture / cv2 unavailable: walkability not checked")
    # absolute map: the dragon / baron spots of a red player must be near the real pits
    pits = {"dragon": (0.6635, 0.7053), "baron": (0.3367, 0.3010)}
    for s in spots:
        if s.objective in pits:
            for team in ("ORDER", "CHAOS"):
                u, v = s.uv_for(team)
                if math.dist((u, v), pits[s.objective]) > 0.15:
                    probs.append(f"{s.id}/{team}: {s.objective} spot {math.dist((u, v), pits[s.objective]):.2f} "
                                 "from its pit (mirrored for the red side?)")
    fae = [s for s in spots if s.faelight]
    late = [s for s in fae if s.after_rift]
    if len(fae) != 12 or len(late) != 4:
        probs.append(f"{len(fae)} Faelights ({len(late)} after the rift transformation), 12 (4) expected")
    game = ((data or {}).get("map") or {}).get("faelights") or {}
    ref_geo = (a.ref or {}).get("geo")
    if ref_geo is not None:
        game = datalib.faelights(ref_geo)
    if game:
        worst = 0.0
        for name, f in game.items():
            near = sorted((math.dist(s.uv_for("ORDER"), tuple(f["uv"])), s.id) for s in fae)
            if not near:
                probs.append(f"Faelight {name} has no ward spot")
                continue
            worst = max(worst, near[0][0])
            if near[0][0] > 0.003:
                probs.append(f"Faelight {name}: nearest spot {near[0][1]} is {near[0][0]:.3f} away from the "
                             "game-file position")
        a.add("INFO", "wards", "faelight-error", f"largest Faelight position error {worst:.3f} of the map width "
              f"(~{worst * 14870:.0f} game units)")
    elif fae:
        a.add("WARN", "wards", "faelights", "no game-file reference: Faelight positions unverified (approximate)")
    ids = [s.id for s in spots]
    if len(set(ids)) != len(ids):
        probs.append("duplicated spot ids")
    for s in spots:
        if not s.label or (getattr(s, "side", None) and not getattr(s, "label_red", "")):
            probs.append(f"{s.id}: label missing for one team")
        if any(r not in POSITIONS for r in s.roles):
            probs.append(f"{s.id}: unknown role")
    if data is not None:
        for d in data.get("spots") or []:
            if any(p not in ("laning", "mid", "late") for p in d.get("phases") or ()):
                probs.append(f"{d.get('id')}: unknown phase")
    if probs:
        a.add("ERROR", "wards", "spots", f"{len(probs)} problems: {_short(probs, 6)}")
    else:
        a.add("INFO", "wards", "spots", f"{len(spots)} spots on walkable pixels for both teams, objective spots at "
              f"their pits, {len(fae)} Faelights ({len(late)} after the rift) at their game-file position")
    camps = ((data or {}).get("map") or {}).get("camps") or []
    try:
        render = importlib.import_module("treeaicoach.render")
        far = []
        for u, v, name in render.CAMPS:
            ref = [c for c in camps if c.get("camp") == ("scuttle" if name == "scuttle" else name)]
            if ref:
                d = min(math.dist((u, v), tuple(c["uv"])) for c in ref)
                if d > 0.015:
                    far.append(f"{name} {d:.3f}")
        if far:
            a.add("WARN", "wards", "camps", f"render.CAMPS differ from the game files: {_short(far)}")
    except Exception:   # noqa: BLE001
        pass


# ----------------------------------------------------------------------------- code tables
def _alias_tables(mod: Any, aliases: set[str]) -> list[tuple[str, list[str]]]:
    out = []
    folded = {x.casefold(): x for x in aliases}
    for name, val in vars(mod).items():
        if name.startswith("__") or not isinstance(val, (frozenset, set, tuple, list, dict)):
            continue
        keys = list(val.keys()) if isinstance(val, dict) else list(val)
        strs = [k for k in keys if isinstance(k, str)]
        if len(strs) < 3 or len(strs) != len(keys):
            continue
        known = [k for k in strs if k in aliases]
        if len(known) < 0.6 * len(strs):
            continue
        unknown = [k for k in strs if k not in aliases]
        if unknown:
            out.append((name, [f"{u} (alias {folded[u.casefold()]})" if u.casefold() in folded else u
                               for u in unknown]))
    return out


def check_code(a: Audit) -> None:
    if not a.aliases or not a.items:
        return
    for mod_name in CODE_MODULES:
        try:
            mod = importlib.import_module(f"treeaicoach.{mod_name}")
        except Exception:   # noqa: BLE001 - optional module (not in every version)
            continue
        for table, unknown in _alias_tables(mod, a.aliases):
            level = "ERROR" if mod_name in DATA_MODULES else "WARN"
            a.add(level, "code", "champion-alias", f"{mod_name}.{table}: unknown champions {_short(unknown)}")
    boots = {int(k) for k, v in a.items.items() if v.get("p") and v.get("k") == "boots"} | {1001}
    try:
        tips = importlib.import_module("treeaicoach.tips")
        tb = set(getattr(tips, "BOOTS", ()) or ())
        if tb:
            missing = sorted(boots - tb)
            if missing:
                a.add("WARN", "code", "boots", f"tips.BOOTS misses {len(missing)} sold boots "
                      f"({_short(a.items[str(i)].get('n') for i in missing)}): a player with tier-3 boots (mid lane "
                      "quest) is told to buy boots; use the item data (k == 'boots')")
            stale = sorted(i for i in tb if not (a.items.get(str(i)) or {}).get("p"))
            if stale:
                a.add("INFO", "code", "boots", f"tips.BOOTS also lists unsold ids {stale} (harmless)")
    except Exception:   # noqa: BLE001
        pass
    try:
        coach = importlib.import_module("treeaicoach.coach")
        major = set(getattr(coach, "MAJOR_ITEM_IDS", ()) or ())
        if major:
            bad = sorted(i for i in major if (a.items.get(str(i)) or {}).get("k") != "legendary"
                         or not (a.items.get(str(i)) or {}).get("p"))
            legend = {int(k) for k, v in a.items.items() if v.get("p") and v.get("k") == "legendary"}
            if bad:
                a.add("WARN", "code", "major-items", f"coach.MAJOR_ITEM_IDS has non-legendary / unsold ids: {bad}")
            miss = sorted(legend - major)
            if miss:
                a.add("INFO", "code", "major-items", f"coach.MAJOR_ITEM_IDS covers {len(major & legend)}/{len(legend)} "
                      f"sold legendaries (not announced: {_short(a.items[str(i)].get('n') for i in miss)})")
    except Exception:   # noqa: BLE001
        pass
    for mod_name, attr in (("reminders", "CONTROL_WARD_ID"), ("tips", "CONTROL_WARD"), ("wards", "CONTROL_WARD"),
                           ("itemization", "CONTROL_WARD")):
        try:
            v = getattr(importlib.import_module(f"treeaicoach.{mod_name}"), attr)
            if (a.items.get(str(v)) or {}).get("en") != "Control Ward":
                a.add("ERROR", "code", "control-ward", f"{mod_name}.{attr} = {v} is not the Control Ward")
        except Exception:   # noqa: BLE001
            pass


def check_versions(a: Audit) -> None:
    items_v = str((a.asset("items.json") or {}).get("version") or "")
    stale = []
    for name in ("champion_meta.json", "item_builds.json", "ward_spots.json"):
        d = a.asset(name)
        if isinstance(d, dict) and d.get("version") and str(d["version"]) != items_v:
            stale.append(f"{name} {d['version']}")
    idx = a.asset("icons/champions/index.json")
    if isinstance(idx, dict) and str(idx.get("patch") or "") != items_v:
        stale.append(f"icons/champions/index.json {idx.get('patch')}")
    if stale:
        a.add("WARN", "versions", "tables", f"built from another Data Dragon version than items.json ({items_v}): "
              f"{_short(stale)}")
    else:
        a.add("INFO", "versions", "tables", f"every table built from Data Dragon {items_v}")


# ----------------------------------------------------------------------------- run / report
CHECKS: tuple[Callable[[Audit], None], ...] = (check_items, check_champions, check_profiles, check_builds,
                                                check_advice, check_matchups, check_objectives, check_atakhan,
                                                check_wards, check_code, check_versions)


def run(root: Path | None = None, offline: bool = True, reference: dict | None = None,
        log: Callable[[str], None] = lambda _m: None) -> list[Finding]:
    """Run every check; ``root`` = the repository to audit (default: this one)."""
    root = Path(root or NEW_ROOT).resolve()
    if str(root) not in sys.path or sys.path[0] != str(root):
        sys.path.insert(0, str(root))
    ref = reference if reference is not None else load_reference(offline, log)
    a = Audit(root, offline, ref, log)
    if ref is None:
        a.add("INFO", "reference", "mode", "offline: bundled data only (no live Data Dragon comparison)")
    elif "error" in ref:
        a.add("WARN", "reference", "live", f"live Data Dragon unavailable ({ref['error']}): internal checks only")
    else:
        a.add("INFO", "reference", "live", f"live Data Dragon {ref['version']} (fr_FR + en_US items / champions)"
              + (", CLASSIC shop" if ref.get("shop") else "") + (", map placeables" if ref.get("geo") else ""))
    for check in CHECKS:
        try:
            check(a)
        except Exception as exc:   # noqa: BLE001 - one broken check must not hide the others
            a.add("ERROR", check.__name__.replace("check_", ""), "crash", f"{type(exc).__name__}: {exc}")
    return a.findings


def table(findings: list[Finding]) -> str:
    order = {lv: i for i, lv in enumerate(LEVELS)}
    rows = sorted(findings, key=lambda f: (order.get(f.level, 9), f.area, f.check))
    out = ["| level | area | check | detail |", "|---|---|---|---|"]
    for f in rows:
        out.append(f"| {f.level} | {f.area} | {f.check} | {f.detail.replace('|', '/')} |")
    counts = {lv: sum(1 for f in findings if f.level == lv) for lv in LEVELS}
    out.append("")
    out.append(f"{counts['ERROR']} errors, {counts['WARN']} warnings, {counts['INFO']} infos")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true", help="bundled data only (no Data Dragon download)")
    ap.add_argument("--root", default=None, help="repository to audit (default: this one)")
    ap.add_argument("--json", default=None, help="also write the findings as JSON")
    ap.add_argument("--quiet", action="store_true", help="only the summary line")
    args = ap.parse_args(argv)
    findings = run(Path(args.root) if args.root else None, offline=args.offline, log=print)
    text = table(findings)
    print(text.splitlines()[-1] if args.quiet else text)
    if args.json:
        Path(args.json).write_text(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    return 1 if any(f.level == "ERROR" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
