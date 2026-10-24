"""Live meta builds per (champion, role): what OP.GG / U.GG show, driven by win-rate data.

Starting items, core items in order, boots, situational items (with win rate / pick numbers),
summoner spells, runes and skill order for my champion and role, refreshed by patch and cached, so
the advice follows the meta without a new release (``docs/LESSONS.md`` 4: game knowledge expires).

Sources (plain HTTPS GET, no key; ``urllib`` only, explicit User-Agent, 5 s timeout, size caps):

* ``ugg`` (primary) - U.GG's per-champion overview JSON (:data:`UGG_URL`): the data behind
  u.gg/lol/champions/<name>/build, ranked solo queue, world, Emerald+ (then Platinum+, then all
  ranks when the role has too few games). Undocumented internal endpoint: the URL version and the
  array layout (:func:`parse_ugg`) can change with their site, and their Cloudflare bot filter
  blocks the default ``curl`` / Python agents (an explicit product User-Agent passes). ONE request
  per champion per patch (all roles in one file). Fragile by nature: everything falls back.
* ``riot`` (fallback, official) - Riot's own in-client recommendations (the runes / summoner
  spells the League client proposes per position), mirrored by CommunityDragon
  (:data:`RIOT_RUNES_URL`). No items, no win rates.
* bundled ``assets/meta_builds.json`` (``tools/fetch_meta_builds.py``): the popular champions x
  roles at build time + the Riot recommendations of every champion, so the exe works offline.
* the rule-based tables of :mod:`treeaicoach.itemization` (``assets/item_builds.json``) when
  nothing above knows the champion.

Cache: ``paths.cache_dir()/meta/<patch>/<alias>.json`` (one file per champion, all roles) +
``riot_runes.json`` + ``names.json`` (French names of runes / spells from Data Dragon). A fetch is
attempted at most once per patch and per day per champion (stamp), always from a daemon thread
(:func:`ensure_async`); :func:`get` never touches the network and never blocks the game thread.
The source and date of every build are logged and kept (``source`` / ``fetched``). Never raises
from its public functions.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

try:
    from treeaicoach import __version__ as _APP_VERSION
except Exception:  # pragma: no cover - defensive
    _APP_VERSION = "1.0.0"

SCHEMA = 1
SOURCE_UGG = "ugg"
SOURCE_RIOT = "riot"
SOURCE_BUNDLED = "bundled"
UGG_URL = "https://stats2.u.gg/lol/1.5/overview/{patch}/ranked_solo_5x5/{key}/1.5.0.json"
RIOT_RUNES_URL = ("https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/"
                  "champion-rune-recommendations.json")
DDRAGON = "https://ddragon.leagueoflegends.com"
USER_AGENT = f"TreeAICoach/{_APP_VERSION} (LoL coach; meta builds, one request per champion per patch)"
TIMEOUT_S = 5.0
MAX_BYTES = 4 * 1024 * 1024          # a U.GG overview ~ 0.5 MB, the Riot recommendations ~ 0.5 MB
RETRY_S = 24 * 3600.0                # a failed / done fetch is not retried within a day (same patch)
CACHE_SUBDIR = "meta"
BUNDLED_FILE = "meta_builds.json"
NAMES_FILE = "names.json"
RIOT_FILE = "riot_runes.json"
STAMP_FILE = "stamps.json"

UGG_REGION = "12"                                       # world
UGG_TIERS = ("14", "10", "8")                           # emerald_plus, platinum_plus, all ranks
UGG_TIER_NAMES = {"14": "Émeraude+", "10": "Platine+", "8": "tous rangs"}
#: the build adapts to the player's own elo: the U.GG rank filters tried per bracket (only the three
#: verified filters; the first one with enough games wins). "" = Emerald+ (default / unranked fallback).
BRACKET_TIERS: dict[str, tuple[str, ...]] = {"": UGG_TIERS, "mid": ("10", "8"), "low": ("8",)}
TIER_BRACKET = {"IRON": "low", "BRONZE": "low", "SILVER": "low", "GOLD": "mid", "PLATINUM": "mid"}
UGG_ROLES = {"JUNGLE": "1", "UTILITY": "2", "BOTTOM": "3", "TOP": "4", "MIDDLE": "5"}
ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
MIN_MATCHES = 200                                       # a role with fewer games: next tier / unknown
ROLE_SHARE = 0.05                                       # a role played less than 5 % of the champion's games is noise
OPTION_SHARE = 0.04                                     # a situational item needs 4 % of the slot's games
MIN_OPTION_GAMES = 60                                   # ... and 60 games (a 100 % win rate on 6 games is noise)
MIN_CORE_GAMES = 300                                    # the core's win rate is printed from 300 games only
MIN_RUNE_GAMES = 150                                    # a rune page under 150 games / 3 % of the role (U.GG
RUNE_SHARE = 0.03                                       # picks pages by win rate): Riot's official page instead
STALE_S = 3 * 24 * 3600.0                               # a cache file older than 3 days is refetched (same patch)
SR_MAP = 11

_lock = threading.RLock()
_mem: dict[tuple[str, str], dict | None] = {}           # (patch, alias) -> champion cache row (None = miss)
_names_mem: dict | None = None
_bundled_mem: dict | None = None
_inflight: set[str] = set()
_fetch_hook: Callable[[str], Any] | None = None         # tests: replaces the HTTP GET
#: last fetch outcomes per alias ("ugg", "riot", "error: ...", "skipped"), for the diagnostics
last_status: dict[str, str] = {}


# ----------------------------------------------------------------------------- model
@dataclass(frozen=True)
class MetaBuild:
    alias: str
    role: str
    patch: str = ""
    source: str = ""                  # "ugg" | "riot" | "bundled:ugg" | "bundled:riot"
    fetched: str = ""                 # ISO date of the data
    tier: str = ""                    # "Émeraude+" ...
    start: tuple[int, ...] = ()
    core: tuple[int, ...] = ()        # in buy order (boots included at their place)
    boots: int | None = None
    slots: tuple[tuple[tuple[int, float, int], ...], ...] = ()   # 4th / 5th / 6th item options (id, wr %, games)
    spells: tuple[int, ...] = ()
    runes: tuple[int, ...] = ()       # 6 perk ids (4 primary + 2 secondary)
    primary_style: int = 0
    secondary_style: int = 0
    shards: tuple[int, ...] = ()
    skill_order: tuple[str, ...] = ()
    skill_max: str = ""               # "EQW"
    wins: int = 0
    matches: int = 0
    core_wr: float = 0.0              # win rate of the core items (%)
    core_games: int = -1              # games of that core (-1: unknown, old cache rows)

    @property
    def core_wr_ok(self) -> float:
        """The core's win rate when it rests on enough games (:data:`MIN_CORE_GAMES`), else 0."""
        return self.core_wr if self.core_wr and (self.core_games < 0 or self.core_games >= MIN_CORE_GAMES) else 0.0

    @property
    def win_rate(self) -> float:
        return round(100.0 * self.wins / self.matches, 1) if self.matches > 0 else 0.0

    @property
    def has_items(self) -> bool:
        return bool(self.core)

    @property
    def options(self) -> tuple[tuple[int, float, int], ...]:
        """Situational items of every slot, best win rate first, without duplicates."""
        seen: set[int] = set()
        out = []
        for slot in self.slots:
            for iid, wr, n in slot:
                if iid not in seen and n >= MIN_OPTION_GAMES:
                    seen.add(iid)
                    out.append((iid, wr, n))
        return tuple(sorted(out, key=lambda x: (-x[1], -x[2])))

    def to_row(self) -> dict[str, Any]:
        return {"start": list(self.start), "core": list(self.core), "boots": self.boots,
                "slots": [[list(o) for o in s] for s in self.slots], "spells": list(self.spells),
                "runes": list(self.runes), "styles": [self.primary_style, self.secondary_style],
                "shards": list(self.shards), "skills": list(self.skill_order), "max": self.skill_max,
                "wins": self.wins, "matches": self.matches, "core_wr": self.core_wr, "core_games": self.core_games,
                "tier": self.tier}


def _ints(xs: Any) -> tuple[int, ...]:
    out = []
    for x in xs if isinstance(xs, (list, tuple)) else ():
        try:
            v = int(x)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out.append(v)
    return tuple(out)


def _f(x: Any, d: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return d
    return v if v == v and v not in (float("inf"), float("-inf")) else d


def build_from_row(alias: str, role: str, row: Any, patch: str = "", source: str = "",
                   fetched: str = "") -> MetaBuild | None:
    """A :class:`MetaBuild` from a cache / bundled row (:meth:`MetaBuild.to_row`). None if invalid."""
    try:
        if not isinstance(row, dict):
            return None
        slots = []
        for s in row.get("slots") or ():
            opts = []
            for o in s if isinstance(s, list) else ():
                if isinstance(o, (list, tuple)) and len(o) >= 3 and int(o[0]) > 0:
                    opts.append((int(o[0]), round(_f(o[1]), 1), int(_f(o[2]))))
            slots.append(tuple(opts))
        styles = _ints(row.get("styles"))
        b = MetaBuild(alias=str(alias), role=str(role), patch=str(patch or row.get("patch") or ""),
                      source=str(source or row.get("source") or ""), fetched=str(fetched or row.get("fetched") or ""),
                      tier=str(row.get("tier") or ""), start=_ints(row.get("start")), core=_ints(row.get("core")),
                      boots=int(row["boots"]) if isinstance(row.get("boots"), int) and row["boots"] > 0 else None,
                      slots=tuple(slots), spells=_ints(row.get("spells")), runes=_ints(row.get("runes")),
                      primary_style=styles[0] if len(styles) > 0 else 0,
                      secondary_style=styles[1] if len(styles) > 1 else 0, shards=_ints(row.get("shards")),
                      skill_order=tuple(str(s) for s in (row.get("skills") or ()) if str(s) in "QWER" and s),
                      skill_max=str(row.get("max") or ""), wins=int(_f(row.get("wins"))),
                      matches=int(_f(row.get("matches"))), core_wr=round(_f(row.get("core_wr")), 1),
                      core_games=int(_f(row.get("core_games"), -1)))
        return b if (b.core or b.runes or b.spells) else None
    except Exception:
        log.debug("meta build row rejected", exc_info=True)
        return None


# ----------------------------------------------------------------------------- parsing (pure)
def patch_key(version: str) -> str:
    """Data Dragon / Live Client version -> patch key of the sources (``"16.20.1"`` -> ``"16_20"``,
    ``"16.20.718.4512"`` -> ``"16_20"``)."""
    parts = [p for p in str(version or "").split(".") if p.isdigit()]
    return f"{parts[0]}_{parts[1]}" if len(parts) >= 2 else ""


def previous_patch(patch: str) -> str:
    """``"16_20"`` -> ``"16_19"`` (``"17_1"`` -> ``"16_24"``); "" when unknown."""
    try:
        a, b = (int(x) for x in str(patch).split("_")[:2])
    except (TypeError, ValueError):
        return ""
    if b > 1:
        return f"{a}_{b - 1}"
    return f"{a - 1}_24" if a > 1 else ""


def _block(x: Any, n: int = 3) -> tuple[int, int, list]:
    """``[matches, wins, payload...]`` of a U.GG block (0s when malformed)."""
    if isinstance(x, list) and len(x) >= n:
        return int(_f(x[0])), int(_f(x[1])), list(x[2:])
    return 0, 0, []


def bracket_of_tier(tier: Any) -> str:
    """The cache bracket of a ranked tier name ("GOLD" -> "mid"; unranked / Emerald and up -> "")."""
    return TIER_BRACKET.get(str(tier or "").strip().upper(), "")


_user_bracket = ""
_user_tier = ""


def set_user_tier(tier: Any) -> None:
    """My own ranked tier ("GOLD", "" = unranked / unknown), read by client_sync: the rank gap of
    the in-game advice (draft_edge.lane_prior)."""
    global _user_tier
    _user_tier = str(tier or "").strip().upper()


def get_user_tier() -> str:
    return _user_tier


def set_bracket(bracket: Any) -> None:
    """The player's own elo bracket: the default of :func:`get` / :func:`ensure_async` from now on."""
    global _user_bracket
    b = str(bracket or "")
    _user_bracket = b if b in BRACKET_TIERS else ""


def get_bracket() -> str:
    return _user_bracket


def _fname(alias: str, bracket: str = "") -> str:
    return f"{alias}~{bracket}.json" if bracket else f"{alias}.json"


def parse_ugg(data: Any, role: str, items: dict[int, Any] | None = None,
              tiers: tuple[str, ...] = UGG_TIERS) -> dict[str, Any] | None:
    """One role of a U.GG overview JSON -> cache row (:meth:`MetaBuild.to_row` format), or None.

    Layout (``data[region][tier][role] = [row, timestamp]``; verified on patch 16.19 / 16.20):
    ``row[0]`` runes ``[games, wins, primary style, secondary style, [6 perks]]``, ``row[1]`` spells,
    ``row[2]`` starting items, ``row[3]`` core items (``[games, wins, [ids]]``), ``row[4]`` skills
    ``[games, wins, [order], "EQW"]``, ``row[5]`` the options of the 4th / 5th / 6th item and the
    consumables (``[[id, wins, games], ...]`` per slot), ``row[6]`` ``[wins, games]``, ``row[8]``
    stat shards. Pure."""
    try:
        rid = UGG_ROLES.get(str(role or "").upper())
        if rid is None or not isinstance(data, dict):
            return None
        region = data.get(UGG_REGION)
        if not isinstance(region, dict):
            return None
        for tier in tiers:
            cell = (region.get(tier) or {}).get(rid) if isinstance(region.get(tier), dict) else None
            if not isinstance(cell, list) or not cell or not isinstance(cell[0], list) or len(cell[0]) < 7:
                continue
            row = cell[0]
            overall = row[6] if isinstance(row[6], list) and len(row[6]) >= 2 else [0, 0]
            wins, games = int(_f(overall[0])), int(_f(overall[1]))
            if games < MIN_MATCHES:
                continue
            rg, rw, rp = _block(row[0], 5)
            styles = _ints(rp[:2]) if len(rp) >= 2 else ()
            perks = _ints(rp[2]) if len(rp) >= 3 else ()
            _sg, _sw, sp = _block(row[1])
            _stg, _stw, st = _block(row[2])
            cg, cw, cp = _block(row[3])
            _kg, _kw, kp = _block(row[4], 4)
            core = _ints(cp[0]) if cp else ()
            boots = None
            if items:
                for i in core:
                    it = items.get(i)
                    if it is not None and getattr(it, "kind", "") == "boots":
                        boots = i
                        break
            slots = []
            for s in (row[5] if isinstance(row[5], list) else [])[:3]:
                opts = []
                tot = sum(int(_f(o[2])) for o in s if isinstance(o, list) and len(o) >= 3)
                for o in s if isinstance(s, list) else ():
                    if not isinstance(o, list) or len(o) < 3:
                        continue
                    iid, w, n = int(_f(o[0])), int(_f(o[1])), int(_f(o[2]))
                    if iid <= 0 or n <= 0 or (tot and n < OPTION_SHARE * tot):
                        continue
                    opts.append([iid, round(100.0 * w / n, 1), n])
                slots.append(sorted(opts, key=lambda x: (-x[1], -x[2])))
            _hg, _hw, hp = _block(row[8]) if len(row) > 8 else (0, 0, [])
            ts = str(cell[1]) if len(cell) > 1 else ""
            return {"start": list(_ints(st[0]) if st else ()), "core": list(core), "boots": boots, "slots": slots,
                    "spells": list(_ints(sp[0]) if sp else ()), "runes": list(perks),
                    "styles": list(styles), "shards": list(_ints(hp[0]) if hp else ()),
                    "skills": [str(k) for k in (kp[0] if kp and isinstance(kp[0], list) else ()) if str(k) in "QWER"],
                    "max": str(kp[1]) if len(kp) > 1 and isinstance(kp[1], str) else "",
                    "wins": wins, "matches": games, "core_wr": round(100.0 * cw / cg, 1) if cg else 0.0,
                    "core_games": cg, "rune_games": rg,
                    "tier": UGG_TIER_NAMES.get(tier, tier), "fetched": ts[:10]}
        return None
    except Exception:
        log.debug("parse_ugg failed", exc_info=True)
        return None


def keep_roles(roles: dict[str, Any]) -> dict[str, Any]:
    """Drop the off-roles: a role is kept with >= :data:`MIN_MATCHES` games and at least
    :data:`ROLE_SHARE` of the champion's games (Ahri jungle with 600 games is noise). Pure."""
    total = sum(int(_f((r or {}).get("matches"))) for r in roles.values())
    return {k: r for k, r in roles.items()
            if int(_f((r or {}).get("matches"))) >= max(MIN_MATCHES, ROLE_SHARE * total)}


def parse_riot(data: Any, champion_key: int, role: str) -> dict[str, Any] | None:
    """Riot's in-client recommendation (runes + spells) of a champion / position on the Rift
    (``champion-rune-recommendations.json``) -> cache row. Pure."""
    try:
        role = str(role or "").upper()
        for c in data if isinstance(data, list) else []:
            if not isinstance(c, dict) or int(_f(c.get("championId"))) != int(champion_key):
                continue
            recs = [r for r in (c.get("runeRecommendations") or [])
                    if isinstance(r, dict) and int(_f(r.get("mapId"))) == SR_MAP]
            same = [r for r in recs if str(r.get("position") or "").upper() == role]
            pick = same or [r for r in recs if r.get("isDefaultPosition")] or recs
            if not pick:
                return None
            r = pick[0]
            perks = _ints(r.get("perkIds"))
            rid = str(r.get("recommendationId") or "")
            return {"runes": list(perks[:6]), "shards": list(perks[6:9]),
                    "styles": [int(_f(r.get("primaryPerkStyleId"))), int(_f(r.get("secondaryPerkStyleId")))],
                    "spells": list(_ints(r.get("summonerSpellIds"))), "fetched": rid[:10] if rid else "",
                    "position": str(r.get("position") or "")}
        return None
    except Exception:
        log.debug("parse_riot failed", exc_info=True)
        return None


def compact_riot(data: Any) -> dict[str, Any]:
    """The whole Riot recommendation file -> ``{champion key: {position: row}}`` (Rift only, ~10x smaller)."""
    out: dict[str, Any] = {}
    for c in data if isinstance(data, list) else []:
        if not isinstance(c, dict):
            continue
        key = int(_f(c.get("championId")))
        rows: dict[str, Any] = {}
        for r in c.get("runeRecommendations") or []:
            if not isinstance(r, dict) or int(_f(r.get("mapId"))) != SR_MAP:
                continue
            pos = str(r.get("position") or "").upper()
            if pos in rows:
                continue
            row = parse_riot([c], key, pos)
            if row:
                row.pop("position", None)
                rows[pos] = row
        if rows and key > 0:
            out[str(key)] = rows
    return out


def names_from_ddragon(runes: Any, spells: Any) -> dict[str, dict[str, str]]:
    """French names ``{"perks": {id: name}, "styles": {id: name}, "spells": {id: name}}`` from Data
    Dragon ``runesReforged.json`` / ``summoner.json``."""
    perks: dict[str, str] = {}
    styles: dict[str, str] = {}
    icons: dict[str, str] = {}        # perk / style id -> Data Dragon image path ("perk-images/...png")
    for tree in runes if isinstance(runes, list) else []:
        if not isinstance(tree, dict):
            continue
        styles[str(tree.get("id"))] = str(tree.get("name") or "")
        if tree.get("icon"):
            icons[str(tree.get("id"))] = str(tree["icon"])
        for slot in tree.get("slots") or []:
            for r in (slot or {}).get("runes") or []:
                if isinstance(r, dict):
                    perks[str(r.get("id"))] = str(r.get("name") or "")
                    if r.get("icon"):
                        icons[str(r.get("id"))] = str(r["icon"])
    sp: dict[str, str] = {}
    sp_img: dict[str, str] = {}       # spell key -> Data Dragon image ("SummonerFlash.png")
    data = spells.get("data") if isinstance(spells, dict) else None
    for v in (data or {}).values():
        if isinstance(v, dict) and str(v.get("key") or "").isdigit():
            sp[str(v["key"])] = str(v.get("name") or "")
            img = (v.get("image") or {}).get("full") if isinstance(v.get("image"), dict) else None
            if img:
                sp_img[str(v["key"])] = str(img)
    return {"perks": perks, "styles": styles, "spells": sp, "perk_icons": icons, "spell_images": sp_img}


# ----------------------------------------------------------------------------- files
def _patch() -> str:
    try:
        from treeaicoach import game_data

        return patch_key(game_data.data_version())
    except Exception:
        return ""


def cache_folder(patch: str) -> Path | None:
    try:
        from treeaicoach import paths

        d = Path(paths.cache_dir()) / CACHE_SUBDIR / (patch or "unknown")
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        log.debug("No meta cache folder", exc_info=True)
        return None


def _read_json(path: Path) -> Any:
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        return json.loads(path.read_bytes().decode("utf-8-sig"))
    except Exception:
        log.debug("Cannot read %s", path, exc_info=True)
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


def bundled() -> dict:
    """The bundled ``assets/meta_builds.json`` (``{}`` when missing)."""
    global _bundled_mem
    with _lock:
        if _bundled_mem is None:
            data = None
            try:
                from treeaicoach.paths import asset_path

                data = _read_json(Path(asset_path(BUNDLED_FILE)))
            except Exception:
                log.debug("bundled meta builds unavailable", exc_info=True)
            _bundled_mem = data if isinstance(data, dict) and isinstance(data.get("champions"), dict) else {}
        return _bundled_mem


def names() -> dict[str, dict[str, str]]:
    """French names of runes / styles / summoner spells (cache of the current patch, else bundled)."""
    global _names_mem
    with _lock:
        if _names_mem is None:
            out = {"perks": {}, "styles": {}, "spells": {}, "perk_icons": {}, "spell_images": {}}
            for src in (bundled().get("names"), _read_json(cache_folder(_patch()) / NAMES_FILE)
                        if cache_folder(_patch()) is not None else None):
                if isinstance(src, dict):
                    for k in out:
                        if isinstance(src.get(k), dict):
                            out[k].update({str(a): str(b) for a, b in src[k].items()})
            _names_mem = out
        return _names_mem


def _champion_key(alias: str) -> int:
    try:
        from treeaicoach.champions import get_default_db

        e = get_default_db().get(alias)
        return int(e.key) if e is not None else 0
    except Exception:
        return 0


def _cached_row(patch: str, alias: str, bracket: str = "") -> dict | None:
    """The champion's cache file of ``patch`` (memoized, misses too)."""
    key = (patch, alias) if not bracket else (patch, f"{alias}~{bracket}")
    with _lock:
        if key in _mem:
            return _mem[key]
    folder = cache_folder(patch)
    data = _read_json(folder / _fname(alias, bracket)) if folder is not None and alias else None
    row = data if isinstance(data, dict) and isinstance(data.get("roles"), dict) else None
    with _lock:
        _mem[key] = row
    return row


def get(alias: str, role: str | None = None, patch: str | None = None, bracket: str | None = None
        ) -> MetaBuild | None:
    """The meta build of ``alias`` in ``role`` (``None``: its most played role): the cache of the
    current patch, else the bundled data (any patch), else None. Never blocks, never raises."""
    try:
        alias = str(alias or "")
        role = str(role or "").upper()
        if not alias:
            return None
        patch = patch if patch is not None else _patch()
        bracket = _user_bracket if bracket is None else str(bracket)
        row = None
        if patch:       # the player's elo bracket first, then Emerald+ (default)
            row = (_cached_row(patch, alias, bracket) if bracket else None) or _cached_row(patch, alias)
        if row is None and patch:
            # patch day: U.GG has no data for the new patch yet; the last patch's cache beats the bundle
            prev = previous_patch(patch)
            if prev:
                row = (_cached_row(prev, alias, bracket) if bracket else None) or _cached_row(prev, alias)
        src_prefix = ""
        if row is None:
            champs = bundled().get("champions") or {}
            row = champs.get(alias) if isinstance(champs.get(alias), dict) else None
            if row is not None:
                row = {"roles": row.get("roles") if isinstance(row.get("roles"), dict) else row,
                       "patch": bundled().get("patch") or "", "source": row.get("source") or bundled().get("source"),
                       "fetched": row.get("fetched") or bundled().get("generated") or ""}
                src_prefix = "bundled:"
        if row is None:
            return _riot_only(alias, role, patch, src_prefix)
        roles = row.get("roles") or {}
        if role not in roles:
            # the role's own official runes / spells beat the items of another role
            riot = _riot_only(alias, role, patch, src_prefix) if role else None
            if riot is not None:
                return riot
            best = max((r for r in roles if r in ROLES), key=lambda r: int(_f((roles[r] or {}).get("matches"))),
                       default=None)
            role = best or role
        b = build_from_row(alias, role, roles.get(role), patch=str(row.get("patch") or patch),
                           source=src_prefix + str(row.get("source") or SOURCE_UGG),
                           fetched=str((roles.get(role) or {}).get("fetched") or row.get("fetched") or ""))
        if b is None:
            return _riot_only(alias, role, patch, src_prefix)
        rg = int(_f((roles.get(role) or {}).get("rune_games"), -1))
        if 0 <= rg < max(MIN_RUNE_GAMES, RUNE_SHARE * b.matches):
            # U.GG shows its best-win-rate page: on a handful of games it is noise (Swain support
            # 16.20: Moisson noire on 71 games of 7 589); Riot's official page of the role instead
            riot = _riot_only(alias, role, patch, src_prefix, exact=True)
            if riot is not None and riot.runes:
                b = replace(b, runes=riot.runes, primary_style=riot.primary_style,
                            secondary_style=riot.secondary_style, shards=riot.shards or b.shards)
        return b
    except Exception:
        log.debug("meta_builds.get failed", exc_info=True)
        return None


def _riot_only(alias: str, role: str, patch: str, src_prefix: str = "", exact: bool = False) -> MetaBuild | None:
    """Runes / spells from Riot's recommendations (cache of the patch, else bundled); ``exact``: only
    Riot's page of that very position (Swain has no support page: his mid Conqueror is no better)."""
    key = _champion_key(alias)
    if key <= 0:
        return None
    folder = cache_folder(patch) if patch else None
    table = _read_json(folder / RIOT_FILE) if folder is not None else None
    src = SOURCE_RIOT
    if not isinstance(table, dict) or str(key) not in table:
        table = bundled().get("riot")
        src = "bundled:" + SOURCE_RIOT
    rows = (table or {}).get(str(key)) if isinstance(table, dict) else None
    if not isinstance(rows, dict) or not rows:
        return None
    if exact and role not in rows:
        return None
    row = rows.get(role) or rows.get(next(iter(rows)))
    return build_from_row(alias, role, row, patch=patch, source=src, fetched=str((row or {}).get("fetched") or ""))


# ----------------------------------------------------------------------------- refresh (network)
def _get_json(url: str) -> Any:
    hook = _fetch_hook
    if hook is not None:
        return hook(url)
    data = get_bytes(url, "application/json")
    if len(data) > MAX_BYTES:
        raise ValueError(f"{url}: response too large")
    return json.loads(data.decode("utf-8"))


_tls_certifi = False      # the system store refused a host once: certifi's bundle from then on


def _certifi_context() -> Any:
    import ssl

    import certifi  # bundled by packaging/treeaicoach.spec (cacert.pem)

    return ssl.create_default_context(cafile=certifi.where())


def get_bytes(url: str, accept: str = "*/*", timeout: float = TIMEOUT_S, limit: int = MAX_BYTES) -> bytes:
    """HTTPS GET (system proxy honoured by urllib). The frozen exe's default TLS context is the
    Windows store; a machine whose store lacks the CA (old Windows, antivirus TLS proxy without its
    root...) refuses U.GG / Data Dragon: retried once with certifi's bundle, which then sticks.
    Raises urllib / ssl errors (callers log them)."""
    global _tls_certifi
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})

    def go(ctx: Any) -> bytes:
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)) if ctx is not None \
            else urllib.request.build_opener()
        with opener.open(req, timeout=timeout) as resp:   # noqa: S310 - fixed https hosts
            return resp.read(limit + 1)

    if _tls_certifi:
        return go(_certifi_context())
    try:
        return go(None)
    except Exception as exc:
        if "certificate" not in (type(exc).__name__ + str(exc)).lower():
            raise
        try:
            ctx = _certifi_context()
        except Exception:
            raise exc from None
        data = go(ctx)
        _tls_certifi = True
        log.info("TLS: the system certificate store refused %s; certifi's bundle is used", url.split("/")[2])
        return data


def _stamps(folder: Path) -> dict:
    st = _read_json(folder / STAMP_FILE)
    return st if isinstance(st, dict) else {}


def _due(folder: Path, key: str, now: float) -> bool:
    try:
        last = float(_stamps(folder).get(key))
    except (TypeError, ValueError):
        return True
    return not (0.0 <= now - last < RETRY_S)


def _stamp(folder: Path, key: str, now: float) -> None:
    st = _stamps(folder)
    st[key] = now
    _write_json(folder / STAMP_FILE, st)


def refresh(alias: str, patch: str | None = None, force: bool = False, fetch: Callable[[str], Any] | None = None,
            now: float | None = None, bracket: str = "") -> str:
    """Blocking fetch of one champion (all roles) for ``patch`` into the cache: "ugg", "riot" (only
    the official recommendations could be fetched), "skipped" (tried less than a day ago) or
    "error: ...". Never raises; call it from a daemon thread (:func:`ensure_async`)."""
    global _fetch_hook
    alias = str(alias or "")
    patch = patch if patch is not None else _patch()
    now = time.time() if now is None else float(now)
    bracket = bracket if bracket in BRACKET_TIERS else ""
    tiers = BRACKET_TIERS[bracket]
    skey = f"{alias}~{bracket}" if bracket else alias
    folder = cache_folder(patch) if patch else None
    if folder is None or not alias:
        last_status[alias] = "error: no patch / cache folder"
        return last_status[alias]
    if not force and not _due(folder, skey, now):
        last_status[alias] = "skipped"
        return "skipped"
    getter = fetch or _get_json
    status = ""
    try:
        key = _champion_key(alias)
        roles: dict[str, Any] = {}
        try:
            items = None
            try:
                from treeaicoach.itemization import load_items

                items = load_items()
            except Exception:
                items = None
            try:
                data = getter(UGG_URL.format(patch=patch, key=key)) if key > 0 else None
            except Exception as exc:
                prev = previous_patch(patch)
                if not prev:
                    raise
                # patch day: U.GG publishes the new patch after a few hours; the last one meanwhile
                log.info("Meta builds (U.GG) patch %s unavailable for %s (%s): patch %s", patch, alias, exc, prev)
                data = getter(UGG_URL.format(patch=prev, key=key))
            for role in ROLES:
                row = parse_ugg(data, role, items, tiers)
                if row:
                    roles[role] = row
            roles = keep_roles(roles)
            if roles:
                status = SOURCE_UGG
        except Exception as exc:
            log.info("Meta builds (U.GG) unavailable for %s: %s", alias, exc)
        riot_path = folder / RIOT_FILE
        if not riot_path.is_file() or force:
            try:
                table = compact_riot(getter(RIOT_RUNES_URL))
                if table:
                    _write_json(riot_path, table)
            except Exception as exc:
                log.info("Riot recommendations unavailable: %s", exc)
        if riot_path.is_file():
            status = status or SOURCE_RIOT
        names_path = folder / NAMES_FILE
        old_names = _read_json(names_path) if names_path.is_file() else None
        if not isinstance(old_names, dict) or not old_names.get("spell_images"):
            try:
                from treeaicoach import game_data

                v = game_data.data_version()
                table = names_from_ddragon(getter(f"{DDRAGON}/cdn/{v}/data/fr_FR/runesReforged.json"),
                                           getter(f"{DDRAGON}/cdn/{v}/data/fr_FR/summoner.json"))
                if table["perks"] and table["spells"]:
                    _write_json(names_path, table)
                    with _lock:
                        global _names_mem
                        _names_mem = None
            except Exception as exc:
                log.info("Rune / spell names unavailable: %s", exc)
        if roles:
            fetched = max((str(r.get("fetched") or "") for r in roles.values()), default="")
            _write_json(folder / _fname(alias, bracket), {"schema": SCHEMA, "patch": patch, "source": SOURCE_UGG,
                                                    "fetched": fetched or time.strftime("%Y-%m-%d"),
                                                    "roles": roles})
            with _lock:
                _mem.pop((patch, skey), None)
            log.info("Meta build of %s (patch %s) from %s, data of %s: roles %s", alias, patch, SOURCE_UGG,
                     fetched, ",".join(roles))
        elif status == SOURCE_RIOT:
            log.info("Meta build of %s (patch %s): only Riot's recommendations (runes / spells)", alias, patch)
        if not status:
            status = "error: no source"
    except Exception as exc:
        log.info("Meta build refresh failed for %s (%s)", alias, exc)
        status = f"error: {type(exc).__name__}"
    _stamp(folder, skey, now)
    last_status[alias] = status
    return status


def _stale(folder: Path | None, alias: str, now: float | None = None, bracket: str = "") -> bool:
    """The champion's cache file is older than :data:`STALE_S` (U.GG data moves during a patch)."""
    try:
        p = folder / _fname(alias, bracket) if folder is not None else None
        return p is not None and p.is_file() and (time.time() if now is None else now) - p.stat().st_mtime > STALE_S
    except OSError:
        return False


def ensure_async(alias: str, role: str | None = None, allow_network: bool = True,
                 bracket: str | None = None) -> threading.Thread | None:
    """Fetch the champion's meta build for the current patch in a daemon thread when the cache has
    none (at most one attempt per day). Returns the thread (tests join it) or None. Never raises."""
    try:
        alias = str(alias or "")
        if not alias or not allow_network:
            return None
        patch = _patch()
        if not patch:
            return None
        bracket = _user_bracket if bracket is None else (bracket if bracket in BRACKET_TIERS else "")
        skey = f"{alias}~{bracket}" if bracket else alias
        row = _cached_row(patch, alias, bracket)
        folder = cache_folder(patch)
        if row is not None and (not role or str(role).upper() in (row.get("roles") or {})) \
                and not _stale(folder, alias, bracket=bracket):
            return None
        if folder is None or not _due(folder, skey, time.time()):
            return None
        with _lock:
            if skey in _inflight:
                return None
            _inflight.add(skey)

        def run() -> None:
            try:
                refresh(alias, patch, bracket=bracket)
            finally:
                with _lock:
                    _inflight.discard(skey)

        t = threading.Thread(target=run, name=f"meta-build-{alias}", daemon=True)
        t.start()
        return t
    except Exception:
        log.debug("ensure_async failed", exc_info=True)
        return None


# ----------------------------------------------------------------------------- use
def next_item(build: MetaBuild, owned: Iterable[int], items: dict[int, Any] | None = None,
              usable: Callable[[int], bool] | None = None) -> tuple[int, str, float] | None:
    """``(item id, why, win rate %)`` : the first core item of the meta build not owned, then the
    best situational option by win rate. Boots are skipped (their timing is the boots rule of
    :mod:`treeaicoach.itemization`). ``usable(id)`` filters (sold on the Rift, not owned, not a
    component of an owned item). Pure, never raises."""
    try:
        own = [int(i) for i in owned or ()]
        ok = usable or (lambda i: i not in own)
        core_done = 0
        for iid in build.core:
            if iid in own or (items is not None and any(o in items and iid in getattr(items[o], "parts", ())
                                                       for o in own)):
                core_done += 1
                continue
            # boots: the boots timing rule; starters (the support quest upgrade heads the support core)
            # and consumables are never "the next item"
            if iid == build.boots or (items is not None and getattr(items.get(iid), "kind", "") in
                                      ("boots", "starter", "consumable")):
                continue
            if ok(iid):
                why = f"objet {core_done + 1} du build du moment"
                if build.core_wr_ok:
                    why += f" ({build.core_wr_ok:g} % de victoires)"
                return iid, why, float(build.core_wr_ok)
        for iid, wr, _n in build.options:
            if ok(iid):
                return iid, f"objet situationnel le plus gagnant ({wr:g} % de victoires)", float(wr)
        return None
    except Exception:
        log.debug("next_item failed", exc_info=True)
        return None


def core_to_buy(build: MetaBuild, items: dict[int, Any] | None = None) -> tuple[int, ...]:
    """The core items one actually buys: starters (the support quest upgrade heads the support
    core) and consumables are left out."""
    if items is None:
        return build.core
    return tuple(i for i in build.core if getattr(items.get(i), "kind", "legendary") not in ("starter", "consumable"))


def _item_name(iid: int, items: dict[int, Any] | None) -> str:
    it = items.get(iid) if items else None
    if it is not None and getattr(it, "name", ""):
        return str(it.name)
    try:
        from treeaicoach import game_data

        return game_data.item_name(iid, str(iid))
    except Exception:
        return str(iid)


def describe(build: MetaBuild | None, items: dict[int, Any] | None = None) -> dict[str, str]:
    """French lines for the UI: ``{"spells", "runes", "start", "core", "options", "skills", "source"}``
    (missing parts are absent). Never raises."""
    out: dict[str, str] = {}
    if build is None:
        return out
    try:
        nm = names()
        if build.spells:
            out["spells"] = " + ".join(nm["spells"].get(str(s), f"sort {s}") for s in build.spells[:2])
        if build.runes:
            key = nm["perks"].get(str(build.runes[0]), "")
            p = nm["styles"].get(str(build.primary_style), "")
            s = nm["styles"].get(str(build.secondary_style), "")
            if key:
                out["runes"] = key + (f" ({p} + {s})" if p and s else f" ({p})" if p else "")
        if build.start:
            out["start"] = " + ".join(_item_name(i, items) for i in build.start[:3])
        if core_to_buy(build, items):
            out["core"] = " → ".join(_item_name(i, items) for i in core_to_buy(build, items)[:3])
        opts = build.options[:3]
        if opts:
            out["options"] = ", ".join(f"{_item_name(i, items)} ({wr:g} %)" for i, wr, _n in opts)
        if build.skill_max:
            out["skills"] = " > ".join(build.skill_max)
        src = {"ugg": "U.GG", "riot": "client Riot", "bundled:ugg": "U.GG (données livrées)",
               "bundled:riot": "client Riot (données livrées)"}.get(build.source, build.source or "?")
        out["source"] = f"{src}, patch {build.patch.replace('_', '.')}" + (f", {build.tier}" if build.tier else "") \
            + (f", {build.fetched}" if build.fetched else "")
    except Exception:
        log.debug("describe failed", exc_info=True)
    return out


def compare(build: MetaBuild | None, final_items: Iterable[int], items: dict[int, Any] | None = None
            ) -> dict[str, Any]:
    """Post-game: my final items vs the meta build: ``{"core_done": [names], "core_missing": [names],
    "options_bought": [(name, wr)], "off_meta": [names], "score": 0..100}``. Pure, never raises."""
    out: dict[str, Any] = {"core_done": [], "core_missing": [], "options_bought": [], "off_meta": [], "score": None}
    try:
        if build is None or not build.core:
            return out
        own = {int(i) for i in final_items or () if int(i) > 0}
        if items:
            own = {i for i in own if i in items and getattr(items[i], "kind", "") in ("legendary", "boots")}
        core = core_to_buy(build, items)
        if not core:
            return out
        for iid in core:
            (out["core_done"] if iid in own else out["core_missing"]).append(_item_name(iid, items))
        opts = {i: wr for i, wr, _n in build.options}
        for iid in sorted(own):
            if iid in build.core:
                continue
            if iid in opts:
                out["options_bought"].append((_item_name(iid, items), opts[iid]))
            elif items is None or getattr(items.get(iid), "kind", "") == "legendary":
                out["off_meta"].append(_item_name(iid, items))
        out["score"] = int(round(100.0 * len(out["core_done"]) / len(core) - 10 * len(out["off_meta"])))
        out["score"] = max(0, min(100, out["score"]))
    except Exception:
        log.debug("compare failed", exc_info=True)
    return out


def data_info() -> dict[str, Any]:
    """Header of the bundled table + cache state (About page / diagnostics)."""
    b = bundled()
    out = {k: b.get(k) for k in ("schema", "version", "patch", "generated", "source") if b.get(k)}
    out["champions"] = len(b.get("champions") or {})
    try:
        folder = cache_folder(_patch())
        out["cached"] = sorted(p.stem for p in folder.glob("*.json")
                               if p.stem not in (NAMES_FILE[:-5], RIOT_FILE[:-5], STAMP_FILE[:-5])) if folder else []
    except Exception:
        out["cached"] = []
    return out


def probe(champions: Iterable[tuple[str, str]] = (("Garen", "TOP"), ("Swain", "UTILITY"))) -> str:
    """Packaging check (``--meta-probe FICHIER``, run on the built exe): TLS stack, a live U.GG fetch
    and a Data Dragon icon download into a throwaway cache, as text. Never raises."""
    lines = []
    try:
        import ssl

        lines.append(f"ssl: {ssl.OPENSSL_VERSION}")
        try:
            import certifi

            lines.append(f"certifi: {certifi.where()}")
        except Exception as exc:
            lines.append(f"certifi: ABSENT ({exc})")
        patch = _patch()
        lines.append(f"patch: {patch} (Data Dragon {_patch_version()})")
        for alias, role in champions:
            t0 = time.monotonic()
            st = refresh(alias, patch, force=True)
            b = get(alias, role)
            lines.append(f"{alias} {role}: {st} in {time.monotonic() - t0:.1f} s; source {b.source if b else None}; "
                         f"core {list(b.core) if b else None}")
        from treeaicoach import item_icons

        b = get("Garen", "TOP")
        if b is not None and b.core:
            item_icons._failed.clear()
            ok = item_icons._download("item", str(b.core[-1]))
            lines.append(f"icon download: {'ok' if ok else 'no url'} -> {item_icons.png_path('item', b.core[-1])}")
        lines.append(f"tls via certifi: {_tls_certifi}")
    except Exception as exc:
        lines.append(f"probe error: {type(exc).__name__}: {exc}")
    return "\n".join(lines) + "\n"


def _patch_version() -> str:
    try:
        from treeaicoach import game_data

        return game_data.data_version()
    except Exception:
        return "?"


def _reset(fetch: Callable[[str], Any] | None = None) -> None:
    """Forget the memoized tables (tests / Data Dragon update) and optionally inject a fetcher."""
    global _names_mem, _bundled_mem, _fetch_hook
    with _lock:
        _mem.clear()
        _names_mem = None
        _bundled_mem = None
        _fetch_hook = fetch
        _inflight.clear()


try:  # a new Data Dragon version = a new patch key: forget the memoized cache rows
    from treeaicoach import game_data as _game_data

    _game_data.add_listener(lambda: _reset(_fetch_hook))
except Exception:  # pragma: no cover - defensive
    log.debug("game_data listener not registered", exc_info=True)


__all__ = ["MetaBuild", "get", "ensure_async", "refresh", "next_item", "describe", "compare", "names",
           "parse_ugg", "parse_riot", "compact_riot", "names_from_ddragon", "patch_key", "build_from_row",
           "data_info", "set_bracket", "get_bracket", "set_user_tier", "get_user_tier", "bracket_of_tier", "UGG_URL", "RIOT_RUNES_URL", "SOURCE_UGG", "SOURCE_RIOT"]
