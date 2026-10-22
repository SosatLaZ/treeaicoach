"""Put the meta build INTO the League client, the way Blitz / Porofessor / OP.GG desktop do (League
Client API, :mod:`treeaicoach.lcu`): the same three things a player does by hand, only faster.

* **Item set** - a "TreeAI · <champion> <role>" item set (start / core in order / boots / situational
  items with their win rate in the block titles) written with ``PUT /lol-item-sets/v1/item-sets/<id>/sets``
  once the champion is locked in: the client shows it in the in-game shop "Recommended" tab, so the
  meta items are highlighted in the shop. The user's own sets are kept (GET, then PUT the whole list;
  never a PUT when the GET failed); our previous "TreeAI" sets are replaced.
* **Runes** - one rune page "TreeAI <champion>" created or replaced and made current. The page limit is
  respected: ours is replaced, a free slot is used, and the page the user is currently editing is only
  overwritten on an explicit click (``consent``).
* **Summoner spells** - ``PATCH /lol-champ-select/v1/session/my-selection``; Flash stays on the key the
  player already uses (read from the current selection).
* **Elo** - the player's own tier (``/lol-ranked/v1/current-ranked-stats``) picks the U.GG rank filter of
  the meta build (:func:`treeaicoach.meta_builds.set_bracket`), so the build adapts to his level.

No game automation: nothing happens in the match, nothing is read from memory, everything is opt-in
(config ``cs_import_items`` default on, ``cs_auto_runes`` / ``cs_auto_spells`` default off, plus manual
buttons). The endpoints could not be tried against a real client (fake-LCU tests only), so everything is
defensive: any failure gives ``Result(False, <message in French>)``, nothing ever raises.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger(__name__)

SESSION_PATH = "/lol-champ-select/v1/session"
SELECTION_PATH = "/lol-champ-select/v1/session/my-selection"
ITEM_SETS_PATH = "/lol-item-sets/v1/item-sets/{sid}/sets"
PAGES_PATH = "/lol-perks/v1/pages"
PAGE_PATH = "/lol-perks/v1/pages/{pid}"
CURRENT_PAGE_PATH = "/lol-perks/v1/currentpage"
INVENTORY_PATH = "/lol-perks/v1/inventory"

SET_PREFIX = "TreeAI"
SET_UID_PREFIX = "treeai-"
PAGE_PREFIX = "TreeAI"
FLASH = 4
SR_MAP = 11
DEFAULT_PAGE_LIMIT = 2              # fallback when the inventory is unreadable (the client gives 2 free pages)
RANK_REFRESH_S = 600.0
RETRY_S = 8.0
MAX_TRIES = 3
ROLE_FR = {"TOP": "top", "JUNGLE": "jungle", "MIDDLE": "mid", "BOTTOM": "tireur", "UTILITY": "support"}


@dataclass(frozen=True)
class Result:
    ok: bool
    msg: str


# ---------------------------------------------------------------------------- pure builders
def _item_name(iid: int) -> str:
    try:
        from treeaicoach import game_data

        return game_data.item_name(iid, str(iid))
    except Exception:
        return str(iid)


def _pct(x: float) -> str:
    return f"{x:.1f}".replace(".", ",") + " %"


def build_item_set(build: Any, alias: str, champion_id: int, role: str = "",
                   name_of: Callable[[int], str] | None = None) -> dict | None:
    """The item set JSON of a :class:`~treeaicoach.meta_builds.MetaBuild` (None without items). Pure."""
    try:
        if build is None or not getattr(build, "core", ()):
            return None
        name_of = name_of or _item_name
        blocks: list[dict] = []

        def block(title: str, ids: Any) -> None:
            ids = [int(i) for i in ids if int(i) > 0]
            if ids:
                blocks.append({"type": title, "items": [{"id": str(i), "count": 1} for i in ids]})

        block("Départ", build.start)
        core = [i for i in build.core if i != build.boots]
        wr = getattr(build, "core_wr_ok", 0.0)
        block("Build dans l'ordre" + (f" · {_pct(wr)} de victoires" if wr else ""), core)
        if build.boots:
            block("Bottes", [build.boots])
        used = set(build.start) | set(build.core)
        for n, slot in enumerate(build.slots[:3], start=4):
            opts = [(i, w, g) for i, w, g in slot if i not in used][:4]
            if opts:
                title = f"Objet {n} · " + " · ".join(f"{name_of(i)} {_pct(w)}" for i, w, _g in opts[:3])
                block(title[:90], [i for i, _w, _g in opts])
        role_fr = ROLE_FR.get(str(role or "").upper(), "")
        title = f"{SET_PREFIX} · {alias}" + (f" {role_fr}" if role_fr else "")
        return {"title": title, "type": "custom", "map": "any", "mode": "any", "sortrank": 0,
                "uid": f"{SET_UID_PREFIX}{alias}-{str(role or 'any').lower()}",
                "associatedChampions": [int(champion_id)] if champion_id else [],
                "associatedMaps": [SR_MAP], "preferredItemSlots": [], "startedFrom": "blank", "blocks": blocks}
    except Exception:
        log.debug("build_item_set failed", exc_info=True)
        return None


def is_ours(item_set: Any) -> bool:
    return isinstance(item_set, dict) and (str(item_set.get("title") or "").startswith(SET_PREFIX)
                                           or str(item_set.get("uid") or "").startswith(SET_UID_PREFIX))


def merge_item_sets(payload: Any, new_set: dict) -> dict | None:
    """The GET payload with our old sets removed and ``new_set`` added (the user's sets kept). None if
    the payload is not what the client returns (then nothing is written). Pure."""
    if not isinstance(payload, dict) or not isinstance(payload.get("itemSets"), list):
        return None
    kept = [s for s in payload["itemSets"] if not is_ours(s)]
    out = dict(payload)
    out["itemSets"] = kept + [new_set]
    return out


def rune_page(build: Any, name: str) -> dict | None:
    """The rune page JSON (4 + 2 perks + 3 shards) of a build, None when incomplete. Pure."""
    try:
        perks = [int(p) for p in (build.runes or ())] + [int(p) for p in (build.shards or ())]
        if len(perks) != 9 or not build.primary_style or not build.secondary_style or min(perks) <= 0:
            return None
        return {"name": name[:25], "primaryStyleId": int(build.primary_style),
                "subStyleId": int(build.secondary_style), "selectedPerkIds": perks, "current": True}
    except Exception:
        return None


def choose_spells(current: tuple[int, int] | None, wanted: Any) -> tuple[int, int] | None:
    """(spell1, spell2) to select: the meta pair, with Flash kept on the slot the player already uses
    (D = spell1, F = spell2; F when unknown). None if fewer than two spells. Pure."""
    try:
        w = [int(x) for x in wanted if int(x) > 0][:2]
        if len(w) < 2 or w[0] == w[1]:
            return None
        if FLASH not in w:
            return w[0], w[1]
        other = w[0] if w[1] == FLASH else w[1]
        if current is not None and current[0] == FLASH:
            return FLASH, other
        return other, FLASH
    except Exception:
        return None


def my_cell(session: Any) -> dict | None:
    try:
        local = int(session.get("localPlayerCellId"))
        return next((c for c in session.get("myTeam") or [] if isinstance(c, dict) and c.get("cellId") == local), None)
    except Exception:
        return None


def current_spells(session: Any) -> tuple[int, int] | None:
    c = my_cell(session)
    try:
        return (int(c.get("spell1Id")), int(c.get("spell2Id"))) if c is not None else None
    except (TypeError, ValueError):
        return None


def is_locked(session: Any) -> bool:
    """My pick is locked in (completed pick action; else a champion id on my cell). Pure."""
    try:
        local = int(session.get("localPlayerCellId"))
        mine = [a for grp in session.get("actions") or [] if isinstance(grp, list) for a in grp
                if isinstance(a, dict) and a.get("actorCellId") == local and a.get("type") == "pick"]
        if mine:
            return any(a.get("completed") is True for a in mine)
        c = my_cell(session)
        return c is not None and int(c.get("championId") or 0) > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------- applier
class ClientSync:
    """Writes the meta build into the client. Thread-safe; every public method returns / never raises."""

    def __init__(self, client: Any = None, cfg_source: Callable[[], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._client = client
        self._cfg_source = cfg_source
        self._clock = clock
        self._lock = threading.RLock()
        self._rank_at = -1e18
        self.tier = ""
        self._done: dict[str, Any] = {}
        self._tries: dict[str, tuple[int, float]] = {}
        self._last: tuple[str, str] | None = None           # (alias, role) of my pick, for the buttons
        self.status: dict[str, str] = {}

    # ------------------------------------------------------------ plumbing
    def _c(self) -> Any:
        if self._client is None:
            from treeaicoach.lcu import get_default_client

            self._client = get_default_client()
        return self._client

    def _flag(self, name: str, default: bool) -> bool:
        try:
            cfg = self._cfg_source() if self._cfg_source is not None else None
            return bool(getattr(cfg, name, default)) and bool(getattr(cfg, "lcu_enabled", True))
        except Exception:
            return default

    def set_config_source(self, fn: Callable[[], Any] | None) -> None:
        self._cfg_source = fn

    def _build(self, alias: str, role: str) -> Any:
        from treeaicoach import itemization as iz

        return iz.meta_build(alias, role or None)

    # ------------------------------------------------------------ elo
    def refresh_rank(self, force: bool = False) -> str:
        """Read my own tier and point the meta build at the matching U.GG rank filter. Cheap, cached."""
        try:
            now = self._clock()
            with self._lock:
                if not force and now < self._rank_at:
                    return self.tier
                self._rank_at = now + RANK_REFRESH_S
            tier = self._c().ranked_tier()
            from treeaicoach import meta_builds

            with self._lock:
                self.tier = tier
            meta_builds.set_bracket(meta_builds.bracket_of_tier(tier))
            return tier
        except Exception:
            log.debug("rank refresh failed", exc_info=True)
            return ""

    # ------------------------------------------------------------ item set
    def apply_items(self, alias: str, role: str = "") -> Result:
        try:
            build = self._build(alias, role)
            if build is None or not build.core:
                return Result(False, "Pas encore de build pour ce champion.")
            from treeaicoach import meta_builds

            cid = meta_builds._champion_key(alias)
            new = build_item_set(build, alias, cid, role)
            if new is None:
                return Result(False, "Build incomplet.")
            c = self._c()
            me = c.current_summoner()
            sid = me.get("summonerId") if isinstance(me, dict) else None
            if not sid:
                return Result(False, "Client LoL introuvable.")
            path = ITEM_SETS_PATH.format(sid=int(sid))
            merged = merge_item_sets(c.get(path), new)
            if merged is None:                       # never write blind: that would wipe the user's sets
                return Result(False, "Impossible de lire tes sets d'objets, rien n'a été modifié.")
            if c.send("PUT", path, merged) is None:
                return Result(False, "Le client a refusé le set d'objets.")
            return Result(True, f"Set d'objets « {new['title']} » ajouté : onglet Recommandés de la boutique.")
        except Exception:
            log.debug("apply_items failed", exc_info=True)
            return Result(False, "Set d'objets non importé.")

    # ------------------------------------------------------------ runes
    def apply_runes(self, alias: str, role: str = "", consent: bool = False) -> Result:
        """Create / replace our page and make it current. ``consent``: may overwrite the editable page
        in use when every slot is taken (explicit click on the button)."""
        try:
            build = self._build(alias, role)
            page = rune_page(build, f"{PAGE_PREFIX} {alias}") if build is not None else None
            if page is None:
                return Result(False, "Pas de page de runes complète pour ce champion.")
            c = self._c()
            pages = c.get(PAGES_PATH)
            if not isinstance(pages, list):
                return Result(False, "Impossible de lire tes pages de runes.")
            editable = [p for p in pages if isinstance(p, dict) and p.get("isEditable", True) and p.get("id") is not None]
            ours = next((p for p in editable if str(p.get("name") or "").startswith(PAGE_PREFIX)), None)
            target = ours
            if target is None:
                inv = c.get(INVENTORY_PATH)
                limit = DEFAULT_PAGE_LIMIT
                if isinstance(inv, dict):
                    try:
                        limit = max(1, int(inv.get("ownedPageCount")))
                    except (TypeError, ValueError):
                        pass
                if len(editable) < limit:
                    created = c.send("POST", PAGES_PATH, page)
                    pid = created.get("id") if isinstance(created, dict) else None
                    if pid is None:
                        return Result(False, "Le client a refusé la page de runes.")
                    return self._set_current(pid, "Page de runes « %s » créée et activée." % page["name"])
                if not consent:
                    return Result(False, "Toutes tes pages de runes sont prises : clique sur « Importer les runes » "
                                         "pour remplacer la page en cours.")
                cur = c.get(CURRENT_PAGE_PATH)
                cur_id = cur.get("id") if isinstance(cur, dict) else None
                target = next((p for p in editable if p.get("id") == cur_id), None) or editable[0] if editable else None
                if target is None:
                    return Result(False, "Aucune page de runes modifiable.")
            body = dict(page)
            if c.send("PUT", PAGE_PATH.format(pid=int(target["id"])), body) is None:
                return Result(False, "Le client a refusé la page de runes.")
            return self._set_current(target["id"], "Page de runes « %s » mise à jour et activée." % page["name"])
        except Exception:
            log.debug("apply_runes failed", exc_info=True)
            return Result(False, "Runes non importées.")

    def _set_current(self, pid: Any, ok_msg: str) -> Result:
        try:
            self._c().send("PUT", CURRENT_PAGE_PATH, int(pid))      # best effort: the page body asked it already
        except Exception:
            pass
        return Result(True, ok_msg)

    # ------------------------------------------------------------ spells
    def apply_spells(self, alias: str, role: str = "") -> Result:
        try:
            build = self._build(alias, role)
            if build is None or len(build.spells) < 2:
                return Result(False, "Pas de sorts conseillés pour ce champion.")
            c = self._c()
            session = c.get(SESSION_PATH)
            if not isinstance(session, dict):
                return Result(False, "Pas en sélection des champions.")
            cur = current_spells(session)
            pair = choose_spells(cur, build.spells)
            if pair is None:
                return Result(False, "Pas de sorts conseillés pour ce champion.")
            if cur == pair:
                return Result(True, "Tes sorts sont déjà les bons.")
            if c.send("PATCH", SELECTION_PATH, {"spell1Id": pair[0], "spell2Id": pair[1]}) is None:
                return Result(False, "Le client a refusé les sorts (trop tard ?).")
            return Result(True, "Sorts mis : Saut éclair reste sur ta touche." if FLASH in pair else "Sorts mis.")
        except Exception:
            log.debug("apply_spells failed", exc_info=True)
            return Result(False, "Sorts non appliqués.")

    # ------------------------------------------------------------ champion select tick
    def tick(self, state: Any, session: Any, client: Any = None) -> None:
        """Called by the champion select watcher on every poll: elo, then the opted-in automatic imports
        once per (pick, role, build). Never raises."""
        try:
            if client is not None:
                self._client = client
            me = getattr(state, "me", None)
            if me is None or not getattr(me, "alias", ""):
                return
            if self._flag("cs_import_items", True) or self._flag("cs_auto_runes", False) \
                    or self._flag("cs_auto_spells", False):
                self.refresh_rank()
            alias, role = me.alias, getattr(me, "position", "") or ""
            with self._lock:
                self._last = (alias, role)
            if not is_locked(session):
                return
            build = self._build(alias, role)
            if build is None:
                return
            sig = (alias, role, build.core, build.tier, build.runes, build.spells)
            for name, flag, fn in (("items", self._flag("cs_import_items", True), lambda: self.apply_items(alias, role)),
                                   ("runes", self._flag("cs_auto_runes", False), lambda: self.apply_runes(alias, role)),
                                   ("spells", self._flag("cs_auto_spells", False), lambda: self.apply_spells(alias, role))):
                if flag:
                    self._once(name, sig, fn)
        except Exception:
            log.debug("client sync tick failed", exc_info=True)

    def _once(self, name: str, sig: Any, fn: Callable[[], Result]) -> None:
        with self._lock:
            if self._done.get(name) == sig:
                return
            n, at = self._tries.get(name + repr(sig), (0, -1e18))
            now = self._clock()
            if n >= MAX_TRIES or now - at < RETRY_S:
                return
            self._tries[name + repr(sig)] = (n + 1, now)
        res = fn()
        with self._lock:
            self.status[name] = res.msg
            if res.ok:
                self._done[name] = sig

    # ------------------------------------------------------------ buttons
    def manual(self, what: str) -> Result:
        """UI button: ``"runes"`` (consent given by the click) or ``"spells"`` / ``"items"``."""
        with self._lock:
            last = self._last
        if last is None:
            return Result(False, "Choisis d'abord ton champion.")
        alias, role = last
        res = {"runes": lambda: self.apply_runes(alias, role, consent=True),
               "spells": lambda: self.apply_spells(alias, role),
               "items": lambda: self.apply_items(alias, role)}.get(what, lambda: Result(False, "?"))()
        with self._lock:
            self.status[what] = res.msg
        return res

    def status_text(self) -> str:
        with self._lock:
            return " ".join(self.status[k] for k in ("items", "runes", "spells") if self.status.get(k))

    def reset(self) -> None:
        with self._lock:
            self._done.clear()
            self._tries.clear()
            self.status.clear()


_default: ClientSync | None = None
_default_lock = threading.Lock()


def get_default() -> ClientSync:
    global _default
    with _default_lock:
        if _default is None:
            _default = ClientSync()
        return _default


__all__ = ["ClientSync", "Result", "build_item_set", "merge_item_sets", "rune_page", "choose_spells",
           "is_locked", "get_default"]
