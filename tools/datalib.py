"""Shared helpers of the game data pipeline (``tools/fetch_*.py``, ``tools/validate_data.py``).

Sources (public, no login, no scraping of sites whose terms forbid it):

* **Riot Data Dragon** (``ddragon.leagueoflegends.com``): versions, ``item.json`` / ``champion.json``
  in fr_FR and en_US. Official static data for developers.
* **CommunityDragon** (``raw.communitydragon.org``): the game files of the live patch, converted to
  JSON - the Riot client champion data (official playstyle ratings, damage / attack type) and the
  Summoner's Rift map data (CLASSIC shop item lists, Faelight / camp placements).
* **Meraki Analytics** (``cdn.merakianalytics.com``, code MIT, data from the League of Legends Wiki
  under CC BY-SA 3.0): champion class roles and usual positions. Cached as they ask.

Every download goes through :func:`get_json` with a cache folder (``training/cache/gamedata``,
git-ignored) so a pipeline run is reproducible offline (``--offline``). Standard library only.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ASSETS = ROOT / "treeaicoach" / "assets"
CACHE = ROOT / "training" / "cache" / "gamedata"
DDRAGON = "https://ddragon.leagueoflegends.com"
CDRAGON = "https://raw.communitydragon.org/latest"
MERAKI = "https://cdn.merakianalytics.com/riot/lol/resources/latest/en-US/champions.json"
UA = "TreeAICoach-data/1.0 (+https://github.com/sosatlaz/treeaicoach; game data pipeline)"
MAP11_BIN = f"{CDRAGON}/game/data/maps/shipping/map11/map11.bin.json"
MAP11_PLACEABLES = f"{CDRAGON}/game/data/maps/mapgeometry/map11/base_srx.materials.bin.json"
CLIENT_CHAMPION = f"{CDRAGON}/plugins/rcp-be-lol-game-data/global/default/v1/champions/{{id}}.json"
#: Game units -> minimap uv (same constants as treeaicoach.render.game_to_uv).
MAP_W, MAP_H = 14870.0, 14980.0


class Offline(RuntimeError):
    """A download was needed in ``--offline`` mode and the cache has no copy."""


def _cache_name(url: str) -> str:
    name = re.sub(r"^https?://", "", url)
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[-180:]


def get_json(url: str, *, offline: bool = False, max_age_s: float | None = None, retries: int = 3,
             timeout: float = 60.0, cache: Path | None = CACHE) -> Any:
    """GET ``url`` as JSON through the cache folder. ``max_age_s`` None = a cached copy is always
    fresh (versioned URLs); otherwise older copies are downloaded again when online."""
    path = (cache / _cache_name(url)) if cache is not None else None
    if path is not None and path.is_file():
        fresh = max_age_s is None or (time.time() - path.stat().st_mtime) < max_age_s
        if fresh or offline:
            return json.loads(path.read_text(encoding="utf-8"))
    if offline:
        raise Offline(url)
    delay = 1.0
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 - fixed https hosts
                raw = resp.read()
            data = json.loads(raw.decode("utf-8"))
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(raw)
                tmp.replace(path)
            return data
        except urllib.error.HTTPError as exc:
            if exc.code == 404 or attempt == retries - 1:
                raise
        except Exception:
            if attempt == retries - 1:
                raise
        time.sleep(delay)
        delay *= 2
    raise RuntimeError("unreachable")


def latest_version(offline: bool = False) -> str:
    """Latest Data Dragon version (``"16.19.1"``); offline: the cached list, else the bundled items."""
    try:
        return str(get_json(f"{DDRAGON}/api/versions.json", offline=offline, max_age_s=3600.0)[0])
    except Offline:
        data = json.loads((ASSETS / "items.json").read_text(encoding="utf-8"))
        return str(data.get("version") or "")


def version_tuple(v: Any) -> tuple[int, ...]:
    """``"16.19.1"`` -> ``(16, 19, 1)``; unparsable -> ``()``."""
    out = []
    for part in str(v or "").split("."):
        m = re.match(r"\d+", part)
        if not m:
            break
        out.append(int(m.group(0)))
    return tuple(out)


def patch_of(version: str) -> str:
    """Data Dragon ``"16.19.1"`` -> game patch ``"26.19"`` (season numbering since 2025: 15 -> 25)."""
    m = re.match(r"(\d+)\.(\d+)", str(version or ""))
    if not m:
        return ""
    major = int(m.group(1))
    return f"{major + 10 if major >= 15 else major}.{int(m.group(2))}"


def ddragon(version: str, kind: str, lang: str, offline: bool = False) -> dict:
    """``data`` dict of ``/cdn/{version}/data/{lang}/{kind}.json`` (kind: item | champion)."""
    return get_json(f"{DDRAGON}/cdn/{version}/data/{lang}/{kind}.json", offline=offline)["data"]


def meraki_champions(offline: bool = False) -> dict:
    return get_json(MERAKI, offline=offline, max_age_s=7 * 86400.0)


def client_champions(ids: Iterable[int], offline: bool = False, workers: int = 8,
                     log: Callable[[str], None] = print) -> dict[int, dict]:
    """Riot client champion JSON (CommunityDragon) per numeric id; missing ones are skipped."""
    ids = sorted({int(i) for i in ids})
    out: dict[int, dict] = {}

    def one(cid: int) -> tuple[int, dict | None]:
        try:
            return cid, get_json(CLIENT_CHAMPION.format(id=cid), offline=offline, max_age_s=6 * 86400.0)
        except Exception as exc:   # noqa: BLE001 - reported, the profile falls back to other sources
            log(f"  client data {cid}: {type(exc).__name__}")
            return cid, None

    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for cid, data in ex.map(one, ids):
            if isinstance(data, dict):
                out[cid] = data
    return out


def game_to_uv(x: float, y: float) -> tuple[float, float]:
    return round(float(x) / MAP_W, 4), round(1.0 - float(y) / MAP_H, 4)


# ----------------------------------------------------------------------------- game files (map 11)
def classic_shop(map_bin: dict) -> set[int]:
    """Item ids of the CLASSIC (normal / ranked Summoner's Rift) shop lists of ``map11.bin``."""
    mode = map_bin.get("Maps/Shipping/Map11/Modes/CLASSIC") or {}
    out: set[int] = set()
    for ref in mode.get("itemLists") or []:
        for item in (map_bin.get(ref) or {}).get("mItems") or []:
            m = re.fullmatch(r"Items/(\d+)", str(item))
            if m:
                out.add(int(m.group(1)))
    return out


def placeables(geo: dict) -> list[dict]:
    """Every map placeable ``{"name", "type", "character", "x", "y"}`` (game units, y = depth)."""
    out: list[dict] = []
    for obj in geo.values():
        if not isinstance(obj, dict) or obj.get("__type") != "MapPlaceableContainer":
            continue
        for item in (obj.get("items") or {}).values():
            if not isinstance(item, dict):
                continue
            tr = item.get("transform")
            if not (isinstance(tr, list) and len(tr) == 4 and isinstance(tr[3], list)):
                continue
            ch = item.get("Character") if isinstance(item.get("Character"), dict) else {}
            rec = str(ch.get("CharacterRecord") or "")
            out.append({"name": str(item.get("name") or ""), "type": str(item.get("__type") or ""),
                        "character": rec.split("/")[1] if rec.startswith("Characters/") else "",
                        "x": float(tr[3][0]), "y": float(tr[3][2])})
    return out


def faelights(geo: dict) -> dict[str, dict]:
    """``FaerieLightsGroup_*`` placements -> ``{"OrderBaseTop": {"x", "y", "uv", "after_rift"}}``.

    The pads of a group share the group origin; the ``SplitPush`` groups are the 4 Faelights that
    only appear once the Elemental Rift has transformed."""
    out: dict[str, dict] = {}
    for p in placeables(geo):
        m = re.fullmatch(r"FaerieLightsGroup_(\w+)", p["name"])
        if m and p["type"] == "MapGroup":
            key = m.group(1)
            out[key] = {"x": round(p["x"], 1), "y": round(p["y"], 1), "uv": list(game_to_uv(p["x"], p["y"])),
                        "after_rift": "SplitPush" in key}
    return out


CAMP_CHARACTERS = {"SRU_Blue": "blue", "SRU_Gromp": "gromp", "SRU_Murkwolf": "wolves", "SRU_Razorbeak": "raptors",
                   "SRU_Red": "red", "SRU_Krug": "krugs", "Sru_Crab": "scuttle", "SRU_Baron": "baron",
                   "SRU_RiftHerald": "herald"}     # the dragons are preloaded off the map (y < 0)


def camps(geo: dict) -> list[dict]:
    """Main monster of each jungle camp / epic pit: ``[{"camp", "x", "y", "uv"}]``."""
    out = []
    for p in placeables(geo):
        name = CAMP_CHARACTERS.get(p["character"])
        if name and 0.0 <= p["x"] <= MAP_W and 0.0 <= p["y"] <= MAP_H:
            out.append({"camp": name, "x": round(p["x"]), "y": round(p["y"]), "uv": list(game_to_uv(p["x"], p["y"]))})
    return out


# ----------------------------------------------------------------------------- output
def write_json(path: Path, data: Any, *, indent: int | None = None) -> None:
    """Atomic UTF-8 JSON write (sorted keys for small diffs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=indent,
                      separators=None if indent else (",", ":"))
    tmp.write_text(text + ("\n" if indent else ""), encoding="utf-8")
    tmp.replace(path)


def read_asset(name: str) -> Any:
    return json.loads((ASSETS / name).read_text(encoding="utf-8"))


def today() -> str:
    return time.strftime("%Y-%m-%d")
