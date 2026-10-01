"""Live game data (items, champions) refreshed from Riot Data Dragon, bundled data as fallback.

League changes every two weeks (new champions, new / renamed items, prices). The app ships a
snapshot (``assets/items.json``, ``assets/icons/champions/index.json``, written by
``tools/fetch_items.py`` / ``tools/fetch_assets.py``) and refreshes it at runtime so that a new
patch does not need a new release:

* :func:`refresh_async` - at startup, in a background daemon thread, at most once per
  :data:`REFRESH_INTERVAL_S` (24 h, stamp file): ``/api/versions.json`` -> if the latest version
  is newer than the cached / bundled one, download ``/cdn/{v}/data/fr_FR/item.json`` +
  ``en_US/item.json`` and ``fr_FR/champion.json`` + ``en_US/champion.json``, convert them to the
  compact bundled formats and store them atomically in ``user_data_dir()/ddragon/``.
  ``urllib`` only, explicit User-Agent, short timeouts, size caps, silent failures (offline is
  normal). Public static data of Riot's official CDN: no game client access at all.
* :func:`items_data` / :func:`champions_data` - the newest of cached and bundled data (by
  Data Dragon version), cached in memory; :func:`item_name` / :func:`champion_name` - French
  names derived from the data (no hard-coded item names elsewhere).
* :func:`add_listener` - callbacks run after an update (itemization / scoreboard / champion
  database reload their tables).
* :func:`data_versions` / :func:`data_versions_text` - the version / patch of every game data
  table in use (items, champions, champion profiles, builds, matchups, ward spots, objectives),
  for the About page and the diagnostics.

Item table details (same conversion for the bundled snapshot and the runtime refresh):

* ``k`` kind - tier-3 boots (built from boots, sometimes without the "Boots" tag) are "boots";
* ``x`` semantic flags read from the English description (:func:`item_flags`: "antiheal",
  "shieldbreak", "anticrit", "stasis", "spellshield", "cleanse", "lifeline", "revive",
  "armorpen", "magicpen", "lethality", "slowresist", "armorshred", "pcthp", "hsp", "antiattack");
* ``p`` - purchasable in a normal Summoner's Rift game. Data Dragon's map flag is too permissive
  (Swiftplay / ARAM starters are flagged for map 11), so ``tools/fetch_items.py`` also intersects
  with the CLASSIC shop lists of the game files (CommunityDragon) and stores the excluded ids as
  ``not_sr``; the runtime refresh carries that list over.

Never raises from its public functions.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

try:
    from treeaicoach import __version__ as _APP_VERSION
except Exception:  # pragma: no cover - defensive
    _APP_VERSION = "1.0.0"

DDRAGON = "https://ddragon.leagueoflegends.com"
LANG = "fr_FR"
SR_MAP = "11"
USER_AGENT = f"TreeAICoach/{_APP_VERSION} (LoL coach; Data Dragon static data)"
TIMEOUT_S = 10.0
MAX_BYTES = 8 * 1024 * 1024          # item.json ~ 0.7 MB, champion.json ~ 0.2 MB
REFRESH_INTERVAL_S = 24 * 3600.0
CACHE_SUBDIR = "ddragon"
ITEMS_FILE = "items.json"
CHAMPIONS_FILE = "champions.json"
STAMP_FILE = "last_check.json"

_lock = threading.RLock()
_items_cache: dict | None = None
_champs_cache: dict | None = None
_listeners: list[Callable[[], None]] = []
_thread: threading.Thread | None = None
_started = False
#: last refresh outcome ("fresh" | "updated" | "skipped" | "error: ..."), for diagnostics
last_status: str = ""


# ----------------------------------------------------------------------------- versions
def version_tuple(v: Any) -> tuple[int, ...]:
    """``"16.19.1"`` -> ``(16, 19, 1)``; unparsable -> ``()`` (older than anything)."""
    out = []
    for part in str(v or "").split("."):
        m = re.match(r"\d+", part)
        if not m:
            break
        out.append(int(m.group(0)))
    return tuple(out)


# ----------------------------------------------------------------------------- conversion (pure)
def classify(item: dict) -> str:
    """Item kind ("legendary", "boots", "starter", "component", "consumable", "trinket", "other").
    Same rules as ``tools/fetch_items.py`` / ``tools/build_items.py`` (scoreboard reads ``k``)."""
    tags = set(item.get("tags") or [])
    gold = item.get("gold") or {}
    total = int(gold.get("total") or 0)
    into = item.get("into") or []
    if "Trinket" in tags:
        return "trinket"
    if "Consumable" in tags:
        return "consumable"
    if "Boots" in tags:
        return "boots" if total > 300 else "component"
    if into:
        return "component"
    if ("Lane" in tags or "Jungle" in tags) and total <= 500:
        return "starter"
    if total >= 2200:
        return "legendary"
    return "other"


def clean_name(name: Any) -> str:
    s = re.sub(r"<br>.*$", "", str(name or ""))
    return re.sub(r"<[^>]+>", "", s).strip()


def purchasable_on_rift(item: dict) -> bool:
    gold = item.get("gold") or {}
    return bool((item.get("maps") or {}).get(SR_MAP)) and bool(gold.get("purchasable")) \
        and item.get("inStore", True) is not False and not item.get("requiredChampion") \
        and not item.get("requiredAlly") and not item.get("hideFromAll")


def _ids(xs: Any) -> list[int]:
    out = []
    for x in xs or []:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


#: Semantic item flags read from the ENGLISH Data Dragon description (tags alone do not say
#: "applies Wounds"). Checked by ``tools/validate_data.py`` against the build tables.
ITEM_FLAG_PATTERNS: tuple[tuple[str, str], ...] = (
    ("antiheal", r"\bWounds\b"),
    ("shieldbreak", r"Shield Reaver"),
    ("anticrit", r"less damage from Critical Strikes"),
    ("stasis", r"Enter Stasis"),
    ("revive", r"Upon taking lethal damage"),
    ("spellshield", r"Spell Shield"),
    ("cleanse", r"Removes? all crowd control"),
    ("lifeline", r"\bLifeline\b"),
    ("armorpen", r"\d+% Armor Penetration"),
    ("magicpen", r"\d+% Magic Penetration"),
    ("lethality", r"\bLethality\b"),
    ("slowresist", r"effectiveness of Slows"),
    ("armorshred", r"reduces the target's Armor"),
    ("pcthp", r"\d% max Health magic damage|percentage of enemy's current Health"),
    ("hsp", r"Heal and Shield Power"),
    ("antiattack", r"Reduce the Attack Speed|damage from Attacks"),
)


def item_flags(item: Any) -> list[str]:
    """Sorted semantic flags of a Data Dragon item (English description), see :data:`ITEM_FLAG_PATTERNS`."""
    try:
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str((item or {}).get("description") or "")))
    except Exception:
        return []
    return sorted(flag for flag, pat in ITEM_FLAG_PATTERNS if re.search(pat, text))


def build_items_table(fr: dict, en: dict, version: str, shop: Iterable[Any] | None = None,
                      exclude: Iterable[Any] | None = None) -> dict:
    """Compact item table (the ``assets/items.json`` format) from the Data Dragon ``data`` dicts.

    ``shop`` (item ids of the CLASSIC Summoner's Rift shop, from the game files) and ``exclude``
    (ids known not to be sold on the Rift, e.g. the ``not_sr`` list of the previous table) lower
    ``p`` to 0 for the items Data Dragon flags for map 11 but a normal game does not sell; those
    ids are listed in ``not_sr``."""
    items: dict[str, dict] = {}
    for key, it in sorted(fr.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
        if not str(key).isdigit() or int(key) >= 10000 or not isinstance(it, dict):   # >= 10000: Arena variants
            continue
        gold = it.get("gold") or {}
        e = en.get(key) or {}
        stats = {k: v for k, v in (it.get("stats") or {}).items()
                 if isinstance(v, (int, float)) and not isinstance(v, bool) and v}
        row = {
            "n": clean_name(it.get("name")),
            "en": clean_name(e.get("name")) or clean_name(it.get("name")),
            "g": int(gold.get("total") or 0),
            "b": int(gold.get("base") or 0),
            "k": classify(it),
            "t": sorted(str(t) for t in (it.get("tags") or [])),
            "s": stats,
            "f": _ids(it.get("from")),
            "i": _ids(it.get("into")),
            "p": 1 if purchasable_on_rift(it) else 0,
        }
        flags = item_flags(e) or item_flags(it)
        if flags:
            row["x"] = flags
        items[str(key)] = row
    for row in items.values():               # tier-3 boots (Gunmetal Greaves has no "Boots" tag)
        if row["k"] != "boots" and any((items.get(str(p)) or {}).get("k") == "boots" for p in row["f"]):
            row["k"] = "boots"
    not_sr: set[int] = set()
    shop_ids = None if shop is None else set(_ids(shop))
    excl = set(_ids(exclude or ()))
    for key, row in items.items():
        if row["p"] and ((shop_ids is not None and int(key) not in shop_ids) or int(key) in excl):
            row["p"] = 0
            not_sr.add(int(key))
    out: dict[str, Any] = {"version": str(version), "lang": LANG, "items": items}
    if not_sr:
        out["not_sr"] = sorted(not_sr)
    return out


def build_champions_table(fr: dict, en: dict, version: str) -> dict:
    """Compact champion list (``{"version", "champions": [{alias, key, name_en, name_fr, tags, st}]}``).

    ``st`` = a few base stats (attack range, health, health per level, move speed) and Riot's
    ``info`` ratings, so a champion released after the build still gets a rule-derived profile
    (:func:`treeaicoach.meta.profile`)."""
    out = []
    for alias, c in sorted(fr.items()):
        if not isinstance(c, dict):
            continue
        e = en.get(alias) or {}
        try:
            key = int(c.get("key") or 0)
        except (TypeError, ValueError):
            key = 0
        row: dict[str, Any] = {"alias": str(c.get("id") or alias), "key": key,
                               "name_en": str(e.get("name") or c.get("name") or alias),
                               "name_fr": str(c.get("name") or e.get("name") or alias),
                               "tags": [str(t) for t in (c.get("tags") or [])]}
        st, info = c.get("stats") or {}, c.get("info") or {}
        try:
            row["st"] = {"ar": int(float(st.get("attackrange") or 0)), "hp": int(float(st.get("hp") or 0)),
                         "hpl": int(float(st.get("hpperlevel") or 0)), "ms": int(float(st.get("movespeed") or 0)),
                         "info": [int(info.get(k) or 0) for k in ("attack", "defense", "magic", "difficulty")]}
        except (TypeError, ValueError):
            pass
        out.append(row)
    return {"version": str(version), "champions": out}


# ----------------------------------------------------------------------------- files
def cache_folder() -> Path | None:
    try:
        from treeaicoach import paths

        d = Path(paths.user_data_dir()) / CACHE_SUBDIR
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        log.debug("No Data Dragon cache folder", exc_info=True)
        return None


def _read_json(path: Path) -> Any:
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        return json.loads(path.read_bytes().decode("utf-8-sig"))
    except Exception:
        log.warning("Cannot read %s; ignored", path, exc_info=True)
        return None


def _write_json(path: Path, data: Any) -> bool:
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=str(path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        tmp = None
        return True
    except Exception:
        log.warning("Cannot write %s", path, exc_info=True)
        return False
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _bundled(*parts: str) -> Any:
    try:
        from treeaicoach.paths import asset_path

        return _read_json(Path(asset_path(*parts)))
    except Exception:
        return None


def _valid_items(d: Any) -> bool:
    return isinstance(d, dict) and isinstance(d.get("items"), dict) and len(d["items"]) >= 50


def _valid_champs(d: Any) -> bool:
    return isinstance(d, dict) and isinstance(d.get("champions"), list) and len(d["champions"]) >= 100


def _newest(cached: Any, bundled: Any, valid: Callable[[Any], bool], vkey: str = "version") -> dict:
    c = cached if valid(cached) else None
    b = bundled if valid(bundled) else None
    if c is not None and (b is None or version_tuple(c.get(vkey)) > version_tuple(b.get(vkey) or b.get("patch"))):
        return c
    return b or c or {}


# ----------------------------------------------------------------------------- data access
def items_data() -> dict:
    """``{"version", "lang", "items": {id: {...}}}``: the newest of the cached and bundled tables."""
    global _items_cache
    with _lock:
        if _items_cache is None:
            folder = cache_folder()
            cached = _read_json(folder / ITEMS_FILE) if folder is not None else None
            _items_cache = _newest(cached, _bundled(ITEMS_FILE), _valid_items)
        return _items_cache


def champions_data() -> dict:
    """``{"version", "champions": [...]}`` from the cache when it is newer than the bundled index."""
    global _champs_cache
    with _lock:
        if _champs_cache is None:
            folder = cache_folder()
            cached = _read_json(folder / CHAMPIONS_FILE) if folder is not None else None
            bundled = _bundled("icons", "champions", "index.json")
            if isinstance(bundled, dict) and "version" not in bundled:
                bundled = dict(bundled, version=bundled.get("patch") or "")
            _champs_cache = _newest(cached, bundled, _valid_champs)
        return _champs_cache


def data_version() -> str:
    """Data Dragon version of the item data in use ("16.19.1")."""
    return str(items_data().get("version") or "")


def item_name(item_id: Any, default: str = "") -> str:
    """French item name from the data (``default`` when unknown). Never raises."""
    try:
        it = (items_data().get("items") or {}).get(str(int(item_id)))
        return str(it.get("n") or default) if isinstance(it, dict) else default
    except Exception:
        return default


def champion_name(alias: Any, default: str = "") -> str:
    """French champion name for a Data Dragon alias ("MonkeyKing" -> "Wukong")."""
    a = str(alias or "").casefold()
    for c in champions_data().get("champions") or []:
        if str(c.get("alias") or "").casefold() == a:
            return str(c.get("name_fr") or c.get("name_en") or default or alias)
    return default or str(alias or "")


#: Bundled derived tables whose header carries a version / patch (``tools/fetch_all.py``).
DERIVED_TABLES: tuple[tuple[str, str], ...] = (
    ("profiles", "champion_meta.json"),
    ("builds", "item_builds.json"),
    ("matchups", "matchups.json"),
    ("wards", "ward_spots.json"),
    ("objectives", "objectives.json"),
)


def _header(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    out = {}
    for k in ("schema", "version", "patch", "generated"):
        v = data.get(k)
        if v in (None, ""):
            v = data.get(f"_{k}") or (data.get("_checked") if k == "generated" else None)   # objectives.json
        if v not in (None, ""):
            out[k] = v
    return out


def data_versions() -> dict[str, dict[str, Any]]:
    """Version / patch of every game data table in use, e.g. ``{"items": {"version": "16.19.1",
    "source": "bundled"}, "profiles": {"schema": 2, "version": "16.19.1", "patch": "26.19"}, ...}``.
    For the About page and the diagnostics. Never raises."""
    out: dict[str, dict[str, Any]] = {}
    try:
        items = items_data()
        bundled = _bundled(ITEMS_FILE)
        src = "bundled" if isinstance(bundled, dict) and bundled.get("version") == items.get("version") else "cache"
        out["items"] = {"version": str(items.get("version") or ""), "source": src,
                        "count": len(items.get("items") or {})}
        champs = champions_data()
        out["champions"] = {"version": str(champs.get("version") or champs.get("patch") or ""),
                            "count": len(champs.get("champions") or [])}
        for name, fname in DERIVED_TABLES:
            out[name] = _header(_bundled(fname))
    except Exception:
        log.debug("data_versions failed", exc_info=True)
    return out


def data_versions_text() -> str:
    """One line: ``"Données du jeu : objets 16.19.1, champions 16.19.1, profils 26.19, ..."``."""
    try:
        v = data_versions()
        names = {"items": "objets", "champions": "champions", "profiles": "profils", "builds": "builds",
                 "matchups": "duels", "wards": "balises", "objectives": "objectifs"}
        parts = []
        for key, label in names.items():
            d = v.get(key) or {}
            ver = d.get("patch") or d.get("version")
            if ver:
                parts.append(f"{label} {ver}")
        return "Données du jeu : " + (", ".join(parts) if parts else "indisponibles")
    except Exception:
        return "Données du jeu : indisponibles"


def invalidate() -> None:
    """Forget the in-memory tables (next access re-reads the files) and notify the listeners."""
    global _items_cache, _champs_cache
    with _lock:
        _items_cache = None
        _champs_cache = None
        listeners = list(_listeners)
    for cb in listeners:
        try:
            cb()
        except Exception:
            log.exception("game_data listener failed")


def add_listener(cb: Callable[[], None]) -> None:
    """``cb()`` is called (from the refresh thread) after new data was stored."""
    with _lock:
        if cb not in _listeners:
            _listeners.append(cb)


# ----------------------------------------------------------------------------- refresh
def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:   # noqa: S310 - fixed https host
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"{url}: response too large")
    return json.loads(data.decode("utf-8"))


def _due(folder: Path, now: float) -> bool:
    st = _read_json(folder / STAMP_FILE)
    try:
        last = float(st.get("checked_at")) if isinstance(st, dict) else None
    except (TypeError, ValueError):
        last = None
    return last is None or not (0.0 <= now - last < REFRESH_INTERVAL_S)


def refresh(force: bool = False, fetch: Callable[[str], Any] | None = None, now: float | None = None) -> str:
    """Blocking refresh (see the module doc). Returns "skipped" (checked < 24 h ago), "fresh"
    (already up to date), "updated" or "error: ...". Never raises."""
    global last_status
    fetch = fetch or _get_json
    now = time.time() if now is None else float(now)
    folder = cache_folder()
    if folder is None:
        last_status = "error: no cache folder"
        return last_status
    try:
        if not force and not _due(folder, now):
            last_status = "skipped"
            return last_status
        versions = fetch(f"{DDRAGON}/api/versions.json")
        latest = str(versions[0]) if isinstance(versions, list) and versions else ""
        if not version_tuple(latest):
            raise ValueError(f"bad versions.json: {str(versions)[:80]}")
        changed = False
        current = items_data()
        if version_tuple(latest) > version_tuple(current.get("version")):
            fr = fetch(f"{DDRAGON}/cdn/{latest}/data/{LANG}/item.json")["data"]
            en = fetch(f"{DDRAGON}/cdn/{latest}/data/en_US/item.json")["data"]
            # the Rift shop filter of the game files is not on Data Dragon: keep the known exclusions
            table = build_items_table(fr, en, latest, exclude=current.get("not_sr") or ())
            if not _valid_items(table):
                raise ValueError("item table too small")
            changed |= _write_json(folder / ITEMS_FILE, table)
        champs = champions_data()
        if version_tuple(latest) > version_tuple(champs.get("version") or champs.get("patch")):
            fr = fetch(f"{DDRAGON}/cdn/{latest}/data/{LANG}/champion.json")["data"]
            en = fetch(f"{DDRAGON}/cdn/{latest}/data/en_US/champion.json")["data"]
            table = build_champions_table(fr, en, latest)
            if not _valid_champs(table):
                raise ValueError("champion table too small")
            changed |= _write_json(folder / CHAMPIONS_FILE, table)
        _write_json(folder / STAMP_FILE, {"checked_at": now, "latest": latest})
        if changed:
            log.info("Data Dragon data updated to %s", latest)
            invalidate()
            last_status = "updated"
        else:
            last_status = "fresh"
        return last_status
    except Exception as exc:
        log.info("Data Dragon refresh unavailable (%s)", exc)
        last_status = f"error: {type(exc).__name__}"
        return last_status


def refresh_async(allow_network: bool = True, force: bool = False) -> threading.Thread | None:
    """Start :func:`refresh` once per process in a daemon thread (no-op when ``allow_network`` is
    false or already started). Returns the thread (tests can join it)."""
    global _thread, _started
    if not allow_network:
        return None
    with _lock:
        if _started and not force:
            return _thread
        _started = True
        try:
            _thread = threading.Thread(target=refresh, kwargs={"force": force}, name="ddragon-refresh",
                                       daemon=True)
            _thread.start()
        except Exception:
            log.exception("Cannot start the Data Dragon refresh")
            _thread = None
        return _thread


__all__ = ["items_data", "champions_data", "item_name", "champion_name", "data_version", "refresh",
           "refresh_async", "add_listener", "invalidate", "build_items_table", "build_champions_table",
           "version_tuple", "item_flags", "data_versions", "data_versions_text"]
