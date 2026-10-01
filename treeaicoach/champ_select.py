"""Pre-game card during champion select (League Client, read-only): my champion, my probable lane
opponent, 3 matchup tips and the recommended starting items.

Data: ONE read-only endpoint of the League Client local API (:mod:`treeaicoach.lcu`, GET only,
loopback only): ``/lol-champ-select/v1/session`` - the champions shown in the champion select
screen (my team's picks / hovers with the assigned positions, the enemy picks once they are
locked). No summoner names / PUUIDs are read or kept (Riot policy: no de-anonymization in champion
select), nothing is ever written to the client, no automation (no auto pick / ban / runes).

* :func:`parse_session` - pure: the session JSON -> :class:`ChampSelectState`.
* :func:`build_card` - pure: the state -> :class:`PregameCard` (title, opponent, 3 tips in plain
  French "action : raison" from :mod:`treeaicoach.game_plan` + :mod:`treeaicoach.meta`, starting
  items with their names and prices from the live item data :mod:`treeaicoach.game_data`).
* :class:`ChampSelectWatcher` - polls the endpoint (at most every :data:`POLL_S`) from the
  caller's thread or its own daemon thread; :meth:`ChampSelectWatcher.card` is what the UI renders
  (``None`` outside champion select). Never raises.

The enemy positions are not shown by the client: the lane opponent is the enemy pick whose usual
position (Meraki data in :mod:`treeaicoach.meta`) matches my assigned position, labelled
"probable". League Classic (champion ids >= 60000) is ignored.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable

log = logging.getLogger(__name__)

SESSION_PATH = "/lol-champ-select/v1/session"
POLL_S = 2.0
CLASSIC_ID_MIN = 60000           # League Classic champion ids (60000 + key)
POSITIONS = {"top": "TOP", "jungle": "JUNGLE", "middle": "MIDDLE", "mid": "MIDDLE", "bottom": "BOTTOM",
             "bot": "BOTTOM", "adc": "BOTTOM", "utility": "UTILITY", "support": "UTILITY"}
ROLE_FR = {"TOP": "top", "JUNGLE": "jungle", "MIDDLE": "mid", "BOTTOM": "tireur", "UTILITY": "support"}
#: 2026 role quests (patch 26.1+): one line per role.
ROLE_QUEST_TIP = {
    "TOP": "Farme ta quête de rôle : Téléportation gratuite et plus d'expérience",
    "JUNGLE": "Nourris ton familier : 35 cumuls finissent ta quête de jungle",
    "MIDDLE": "Quête de rôle : bottes niveau 3 et rappel plus rapide",
    "BOTTOM": "Finis ta quête de rôle : tes bottes libèrent un emplacement",
    "UTILITY": "Finis ta quête de support : balises de contrôle moins chères",
}
#: Starting items per start kind and why (Data Dragon ids; names / prices read from the live data):
#: the builds table of :mod:`treeaicoach.itemization` (``assets/item_builds.json``).
from treeaicoach.itemization import START_ITEMS, START_WHY  # noqa: E402  (re-exported)

MAX_WORDS = 12


@dataclass(frozen=True)
class Pick:
    alias: str
    name: str
    position: str = ""            # "TOP" | ... | "" (unknown)
    locked: bool = True


@dataclass(frozen=True)
class ChampSelectState:
    me: Pick | None
    allies: tuple[Pick, ...] = ()
    enemies: tuple[Pick, ...] = ()
    phase: str = ""               # LCU timer phase ("PLANNING", "BAN_PICK", "FINALIZATION"...)


@dataclass(frozen=True)
class PregameCard:
    title: str                              # "AHRI · MID"
    my_alias: str
    my_name: str
    role: str                               # "MIDDLE" | ... | ""
    opponent: str | None = None             # enemy champion name (probable lane opponent)
    opponent_alias: str | None = None
    opponent_sure: bool = False             # False: guessed from the champion's usual position
    tips: tuple[str, ...] = ()              # 3 short "action : raison" lines
    start_items: tuple[tuple[int, str, int], ...] = ()   # (id, French name, gold)
    start_why: str = ""
    start_gold: int = 0
    phase: str = ""
    lines: tuple[str, ...] = field(default=())   # ready-to-render text lines (title excluded)


# ----------------------------------------------------------------------------- parsing (pure)
def _int(x: Any) -> int:
    try:
        return int(x)
    except (TypeError, ValueError, OverflowError):
        return 0


def _default_resolver() -> Callable[[int], tuple[str, str] | None]:
    def resolve(cid: int) -> tuple[str, str] | None:
        try:
            from treeaicoach.champions import get_default_db

            e = get_default_db().by_key(cid)
            return (e.alias, e.name_fr) if e is not None else None
        except Exception:
            return None
    return resolve


def _pick(cell: Any, resolve: Callable[[int], tuple[str, str] | None]) -> Pick | None:
    if not isinstance(cell, dict):
        return None
    cid = _int(cell.get("championId"))
    locked = cid > 0
    if cid <= 0:
        cid = _int(cell.get("championPickIntent"))
    if cid <= 0 or cid >= CLASSIC_ID_MIN:
        return None
    r = resolve(cid)
    if r is None:
        return None
    pos = POSITIONS.get(str(cell.get("assignedPosition") or "").strip().lower(), "")
    return Pick(alias=r[0], name=r[1] or r[0], position=pos, locked=locked)


def parse_session(session: Any, resolve: Callable[[int], tuple[str, str] | None] | None = None
                  ) -> ChampSelectState | None:
    """Champion select session JSON -> state (None when not a usable session). Never raises."""
    try:
        if not isinstance(session, dict) or session.get("isSpectating") is True:
            return None
        resolve = resolve or _default_resolver()
        my_team = session.get("myTeam") if isinstance(session.get("myTeam"), list) else []
        their = session.get("theirTeam") if isinstance(session.get("theirTeam"), list) else []
        if any(_int(c.get("championId")) >= CLASSIC_ID_MIN for c in my_team + their if isinstance(c, dict)):
            return None                                   # League Classic: not supported
        local = _int(session.get("localPlayerCellId"))
        me = None
        allies: list[Pick] = []
        for c in my_team:
            p = _pick(c, resolve)
            if isinstance(c, dict) and _int(c.get("cellId")) == local:
                me = p if p is not None else Pick("", "", POSITIONS.get(
                    str(c.get("assignedPosition") or "").lower(), ""), False)
            elif p is not None:
                allies.append(p)
        enemies = tuple(p for p in (_pick(c, resolve) for c in their) if p is not None and p.locked)
        timer = session.get("timer") if isinstance(session.get("timer"), dict) else {}
        return ChampSelectState(me=me, allies=tuple(allies), enemies=enemies, phase=str(timer.get("phase") or ""))
    except Exception:
        log.debug("parse_session failed", exc_info=True)
        return None


# ----------------------------------------------------------------------------- card (pure)
def _profile(alias: str) -> Any:
    from treeaicoach import meta
    return meta.profile(alias)


ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")


def assign_roles(enemies: tuple[Pick, ...]) -> dict[str, tuple[Pick, bool]]:
    """role -> (enemy pick, sure): assigned positions are kept (sure); the others get the free roles
    that best match their usual positions (Meraki ranks; exhaustive over <= 120 permutations)."""
    from itertools import permutations
    out: dict[str, tuple[Pick, bool]] = {}
    rest: list[Pick] = []
    for e in enemies:
        if e.position in ROLES and e.position not in out:
            out[e.position] = (e, True)
        else:
            rest.append(e)
    free = [r for r in ROLES if r not in out]
    rest = rest[:len(free)]
    if not rest:
        return out

    def cost(e: Pick, role: str) -> int:
        pos = tuple("UTILITY" if p == "SUPPORT" else p for p in _profile(e.alias).positions)  # Meraki names
        return pos.index(role) if role in pos else 4

    best = min(permutations(free, len(rest)), key=lambda perm: sum(cost(e, r) for e, r in zip(rest, perm)))
    for e, r in zip(rest, best):
        out[r] = (e, False)
    return out


def lane_opponent(role: str, enemies: tuple[Pick, ...]) -> tuple[Pick | None, bool]:
    """(enemy pick, sure) for my role: an enemy with that assigned position (sure), else the enemy
    the role assignment of :func:`assign_roles` puts there (probable)."""
    if role not in ROLES:
        return None, False
    got = assign_roles(enemies).get(role)
    return (got[0], got[1]) if got else (None, False)


def _fit(line: str | None) -> str | None:
    """The line if it is short enough (the "(probable ...)" detail dropped first), else None:
    a tip is never cut in the middle of a sentence."""
    if not line:
        return None
    if len(line.split()) > MAX_WORDS and " (" in line:
        line = line.split(" (", 1)[0]
    return line if len(line.split()) <= MAX_WORDS else None


def start_kind(alias: str, role: str) -> str:
    """Key of :data:`START_ITEMS` for my champion / role."""
    from treeaicoach import itemization as iz
    cls = iz.champion_class(alias, role or None)
    if role == "UTILITY":
        return "support"
    if role == "JUNGLE":
        if cls in ("tank", "support_tank"):
            return "jungle_tank"
        m = _profile(alias)
        return "jungle_mobile" if cls.startswith("assassin") and int(m.ratings[3]) >= 3 else "jungle"
    if cls == "marksman":
        return "marksman"
    if cls in ("mage", "assassin_ap", "enchanter", "fighter_ap"):
        return "mage"
    if cls in ("tank", "support_tank"):
        return "tank"
    return "fighter"


def start_items(alias: str, role: str) -> tuple[tuple[tuple[int, str, int], ...], str]:
    """((id, name, gold) ..., why) from the live item data; unknown / unbuyable ids are skipped."""
    from treeaicoach import itemization as iz
    kind = start_kind(alias, role)
    items = iz.load_items()
    out = []
    for iid in iz.START_ITEMS.get(kind, ()):
        it = items.get(iid)
        if it is not None and it.rift:
            out.append((iid, it.name, it.gold))
    return tuple(out), iz.START_WHY.get(kind, "")


def build_card(state: ChampSelectState | None) -> PregameCard | None:
    """The pre-game card (None without my champion). Pure, never raises."""
    try:
        if state is None or state.me is None or not state.me.alias:
            return None
        from treeaicoach import game_plan
        me = state.me
        role = me.position
        opp, sure = lane_opponent(role, state.enemies) if role != "JUNGLE" else (None, False)
        tips: list[str] = []
        if opp is not None:
            tips += game_plan.lane_lines(me.alias, opp.alias, opp.name)
        elif role == "JUNGLE" and state.enemies:
            ns = [SimpleNamespace(champion_alias=e.alias, champion_name=e.name,
                                  position=e.position or (tuple(_profile(e.alias).positions) or ("",))[0])
                  for e in state.enemies]
            fg = game_plan.jungler_first_gank(ns)
            if fg:
                tips.append(f"Premier gank {fg[0]} : {fg[1]} a du mal à s'échapper")
        jg_pick = assign_roles(state.enemies).get("JUNGLE") if len(state.enemies) >= 3 or any(
            e.position == "JUNGLE" for e in state.enemies) else None
        enemy_jg = jg_pick[0] if jg_pick else None
        if enemy_jg is not None and role in ("TOP", "MIDDLE", "BOTTOM", "UTILITY"):
            jl = game_plan.jungle_line(SimpleNamespace(champion_alias=enemy_jg.alias, champion_name=enemy_jg.name),
                                       None, role)
            if jl:
                tips.append(jl)
        if role in ROLE_QUEST_TIP:
            tips.append(ROLE_QUEST_TIP[role])
        tips = [t for t in (_fit(x) for x in tips) if t]
        if len(tips) < 3 and role != "JUNGLE":
            tips.append("Tue vite la première vague : le premier niveau 2 gagne")
        if len(tips) < 3 and role == "JUNGLE":
            tips.append("Balise ta jungle à 2:30 : contre-gank si leur jungler arrive")
        uniq: list[str] = []
        for t in tips:
            if t not in uniq:
                uniq.append(t)
        tips = uniq[:3]
        items, why = start_items(me.alias, role)
        gold = sum(g for _i, _n, g in items)
        title = f"{me.name.upper()} · {ROLE_FR.get(role, '?').upper()}" if role else me.name.upper()
        lines: list[str] = []
        if opp is not None:
            lines.append(f"Face à {opp.name}" + ("" if sure else " (probable)"))
        lines += tips
        if items:
            lines.append("Départ : " + " + ".join(n for _i, n, _g in items) + f" ({gold} PO)")
        return PregameCard(title=title[:40], my_alias=me.alias, my_name=me.name, role=role,
                           opponent=opp.name if opp else None, opponent_alias=opp.alias if opp else None,
                           opponent_sure=sure, tips=tuple(tips), start_items=items, start_why=why,
                           start_gold=gold, phase=state.phase, lines=tuple(lines))
    except Exception:
        log.debug("build_card failed", exc_info=True)
        return None


# ----------------------------------------------------------------------------- watcher
class ChampSelectWatcher:
    """Polls the champion select session (read-only) and keeps the current card. Thread-safe.

    ``client`` is a :class:`treeaicoach.lcu.LcuClient` (default: the shared one). Use
    :meth:`poll` from an existing loop, or :meth:`start` for a daemon thread; :meth:`card`
    returns the latest card (None outside champion select)."""

    def __init__(self, client: Any = None, resolve: Callable[[int], tuple[str, str] | None] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._client = client
        self._resolve = resolve
        self._clock = clock
        self._lock = threading.Lock()
        self._card: PregameCard | None = None
        self._next = -1e18
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            from treeaicoach.lcu import get_default_client
            self._client = get_default_client()
        return self._client

    def poll(self, force: bool = False) -> PregameCard | None:
        """One poll (rate-limited to :data:`POLL_S` unless ``force``); returns the current card."""
        try:
            now = self._clock()
            with self._lock:
                if not force and now < self._next:
                    return self._card
                self._next = now + POLL_S
            client = self._get_client()
            session = client.get(SESSION_PATH) if client is not None else None
            card = build_card(parse_session(session, self._resolve)) if session is not None else None
            with self._lock:
                self._card = card
            return card
        except Exception:
            log.debug("champ select poll failed", exc_info=True)
            return None

    def card(self) -> PregameCard | None:
        with self._lock:
            return self._card

    def start(self) -> None:
        """Poll in a daemon thread until :meth:`stop`."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="champ-select", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll(force=True)
            self._stop.wait(POLL_S)


_default: ChampSelectWatcher | None = None
_default_lock = threading.Lock()


def get_default_watcher() -> ChampSelectWatcher:
    """Process-wide watcher (on the shared LCU client)."""
    global _default
    with _default_lock:
        if _default is None:
            _default = ChampSelectWatcher()
        return _default


def pregame_card() -> PregameCard | None:
    """Convenience for the UI: poll the shared watcher (rate-limited) and return the card."""
    return get_default_watcher().poll()


__all__ = ["ChampSelectState", "Pick", "PregameCard", "parse_session", "build_card", "lane_opponent",
           "start_items", "ChampSelectWatcher", "get_default_watcher", "pregame_card", "SESSION_PATH"]
