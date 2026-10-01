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
from typing import Any, Callable

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


def build_items_table(fr: dict, en: dict, version: str) -> dict:
    """Compact item table (the ``assets/items.json`` format) from the Data Dragon ``data`` dicts."""
    items: dict[str, dict] = {}
    for key, it in sorted(fr.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
        if not str(key).isdigit() or int(key) >= 10000 or not isinstance(it, dict):   # >= 10000: Arena variants
            continue
        gold = it.get("gold") or {}
        e = en.get(key) or {}
        stats = {k: v for k, v in (it.get("stats") or {}).items()
                 if isinstance(v, (int, float)) and not isinstance(v, bool) and v}
        items[str(key)] = {
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
    return {"version": str(version), "lang": LANG, "items": items}


def build_champions_table(fr: dict, en: dict, version: str) -> dict:
    """Compact champion list (``{"version", "champions": [{alias, key, name_en, name_fr, tags}]}``)."""
    out = []
    for alias, c in sorted(fr.items()):
        if not isinstance(c, dict):
            continue
        e = en.get(alias) or {}
        try:
            key = int(c.get("key") or 0)
        except (TypeError, ValueError):
            key = 0
        out.append({"alias": str(c.get("id") or alias), "key": key,
                    "name_en": str(e.get("name") or c.get("name") or alias),
                    "name_fr": str(c.get("name") or e.get("name") or alias),
                    "tags": [str(t) for t in (c.get("tags") or [])]})
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
        if version_tuple(latest) > version_tuple(items_data().get("version")):
            fr = fetch(f"{DDRAGON}/cdn/{latest}/data/{LANG}/item.json")["data"]
            en = fetch(f"{DDRAGON}/cdn/{latest}/data/en_US/item.json")["data"]
            table = build_items_table(fr, en, latest)
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
           "version_tuple"]
