"""Item / summoner spell / rune icons (Data Dragon images) for the build highlight.

The in-game item card (:mod:`treeaicoach.overlay_render`), the champion select card
(:mod:`treeaicoach.ui_page_home`) and the post-game report show the recommended item with its
icon, like OP.GG. Sources, in order:

1. the download cache ``paths.cache_dir()/icons/<kind>/<name>.png`` (Data Dragon images of the
   current version, fetched by :func:`prefetch` in a daemon thread: never on the game / UI thread);
2. the bundled ``assets/icons/{item,spell,perk}/`` (``tools/fetch_meta_builds.py --icons``: every
   item of the bundled meta builds + the summoner spells + the keystones, 48 px), so the card has
   its icon offline and on the very first game.

Nothing found: None (the callers draw the text without icon). Never raises.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

DDRAGON = "https://ddragon.leagueoflegends.com"
KINDS = ("item", "spell", "perk")
MAX_ICON_BYTES = 256 * 1024
BUNDLED_PX = 48
_lock = threading.Lock()
_rgba: "OrderedDict[tuple[str, str], Any]" = OrderedDict()
_RGBA_MAX = 96
_inflight: set[tuple[str, str]] = set()
_failed: set[tuple[str, str]] = set()          # one download attempt per icon and session
_fetch_hook: Any = None                         # tests: replaces the HTTP GET (url -> bytes)


def _safe(name: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(name or ""))[:120]


def _names() -> dict:
    try:
        from treeaicoach import meta_builds

        return meta_builds.names()
    except Exception:
        return {}


def file_name(kind: str, key: Any) -> str:
    """File name of an icon: items by id, spells by key ("4" -> "SummonerFlash.png"), perks by id."""
    key = str(key)
    if kind == "spell":
        img = (_names().get("spell_images") or {}).get(key)
        return _safe(img) if img else ""
    return f"{_safe(key)}.png" if key.isdigit() else ""


def url(kind: str, key: Any) -> str:
    """Data Dragon URL of an icon ("" when unknown)."""
    try:
        from treeaicoach import game_data

        ver = game_data.data_version()
    except Exception:
        ver = ""
    key = str(key)
    if kind == "item" and key.isdigit() and ver:
        return f"{DDRAGON}/cdn/{ver}/img/item/{key}.png"
    if kind == "spell" and ver:
        img = (_names().get("spell_images") or {}).get(key)
        return f"{DDRAGON}/cdn/{ver}/img/spell/{img}" if img else ""
    if kind == "perk":
        path = (_names().get("perk_icons") or {}).get(key)
        return f"{DDRAGON}/cdn/img/{path}" if path else ""
    return ""


def _cache_dir(kind: str) -> Path | None:
    try:
        from treeaicoach import paths

        return Path(paths.cache_dir()) / "icons" / kind
    except Exception:
        return None


def _bundled_dir(kind: str) -> Path | None:
    try:
        from treeaicoach.paths import asset_path

        return Path(asset_path("icons", kind))
    except Exception:
        return None


def png_path(kind: str, key: Any) -> Path | None:
    """The icon file on disk (download cache first, then bundled), None when absent."""
    name = file_name(kind, key)
    if not name:
        return None
    for d in (_cache_dir(kind), _bundled_dir(kind)):
        try:
            if d is not None and (d / name).is_file():
                return d / name
        except OSError:
            continue
    return None


def png_bytes(kind: str, key: Any) -> bytes | None:
    """PNG bytes of an icon (report data URIs, Qt pixmaps), None when absent. Never raises."""
    p = png_path(kind, key)
    try:
        if p is not None and p.stat().st_size <= MAX_ICON_BYTES:
            return p.read_bytes()
    except OSError:
        pass
    return None


def rgba(kind: str, key: Any) -> Any:
    """Decoded RGBA ``np.ndarray`` of an icon (memoized), None when absent. Never raises."""
    k = (str(kind), str(key))
    with _lock:
        if k in _rgba:
            _rgba.move_to_end(k)
            return _rgba[k]
    img = None
    data = png_bytes(kind, key)
    if data:
        try:
            import cv2
            import numpy as np

            raw = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
            if raw is not None and raw.ndim == 3 and raw.shape[2] == 4:
                img = cv2.cvtColor(raw, cv2.COLOR_BGRA2RGBA)
            elif raw is not None and raw.ndim == 3:
                img = cv2.cvtColor(raw, cv2.COLOR_BGR2RGBA)
            if img is not None:
                img.setflags(write=False)
        except Exception:
            log.debug("icon %s/%s not decodable", kind, key, exc_info=True)
            img = None
    if img is None:
        return None          # not memoized: a download may bring it a moment later
    with _lock:
        _rgba[k] = img
        while len(_rgba) > _RGBA_MAX:
            _rgba.popitem(last=False)
    return img


def item_rgba(item_id: Any) -> Any:
    return rgba("item", item_id)


def _get(u: str) -> bytes:
    if _fetch_hook is not None:
        return _fetch_hook(u)
    from treeaicoach.meta_builds import get_bytes

    return get_bytes(u, "image/png,*/*", limit=MAX_ICON_BYTES)


def _download(kind: str, key: str) -> bool:
    name, u, d = file_name(kind, key), url(kind, key), _cache_dir(kind)
    if not name or not u or d is None:
        return False
    data = _get(u)
    if not data or len(data) > MAX_ICON_BYTES or not data.startswith(b"\x89PNG"):
        raise ValueError(f"{u}: not a PNG")
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{name}.tmp"
    tmp.write_bytes(data)
    tmp.replace(d / name)
    with _lock:
        _rgba.pop((kind, key), None)
    return True


def missing(kind: str, keys: Iterable[Any]) -> list[str]:
    """The icons of ``keys`` neither downloaded nor bundled (nor already tried this session)."""
    out = []
    for k in keys or ():
        k = str(k)
        cache = _cache_dir(kind)
        name = file_name(kind, k)
        if not name or (kind, k) in _failed or (kind, k) in _inflight:
            continue
        try:
            if cache is not None and (cache / name).is_file():
                continue
        except OSError:
            continue
        if png_path(kind, k) is not None and kind != "item":
            continue                     # spells / runes barely change: the bundled one is fine
        if k not in out:
            out.append(k)
    return out


def prefetch(wanted: dict[str, Iterable[Any]], allow_network: bool = True) -> threading.Thread | None:
    """Download the missing icons ``{"item": [ids], "spell": [keys], "perk": [ids]}`` of the current
    Data Dragon version in a daemon thread (one attempt per icon per session). Never raises."""
    try:
        if not allow_network:
            return None
        todo = [(kind, k) for kind in KINDS for k in missing(kind, wanted.get(kind) or ())]
        if not todo:
            return None
        with _lock:
            todo = [t for t in todo if t not in _inflight]
            _inflight.update(todo)
        if not todo:
            return None

        def run() -> None:
            ok = 0
            for kind, k in todo:
                try:
                    ok += int(_download(kind, k))
                except Exception as exc:
                    log.debug("icon %s/%s not downloaded: %s", kind, k, exc)
                    _failed.add((kind, k))
                finally:
                    with _lock:
                        _inflight.discard((kind, k))
            if ok:
                log.info("Build icons: %d downloaded", ok)

        t = threading.Thread(target=run, name="item-icons", daemon=True)
        t.start()
        return t
    except Exception:
        log.debug("icon prefetch not started", exc_info=True)
        return None


def build_icons(build: Any, items: dict | None = None) -> dict[str, list]:
    """The icons a meta build shows: ``{"item": start + core + options, "spell": spells, "perk":
    keystone + styles}``."""
    try:
        it = list(dict.fromkeys(list(build.start) + list(build.core) + [i for i, _w, _n in build.options[:3]]))
        perks = [p for p in (build.runes[:1] + (build.primary_style, build.secondary_style)) if p]
        return {"item": it, "spell": list(build.spells[:2]), "perk": perks}
    except Exception:
        return {}


def _reset(fetch: Any = None) -> None:
    """Tests: forget the memoized icons / attempts, optionally inject a fetcher."""
    global _fetch_hook
    with _lock:
        _rgba.clear()
        _inflight.clear()
        _failed.clear()
        _fetch_hook = fetch


__all__ = ["png_path", "png_bytes", "rgba", "item_rgba", "prefetch", "url", "file_name", "build_icons", "missing"]
