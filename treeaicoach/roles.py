"""Role of every player (both teams): TOP / JUNGLE / MIDDLE / BOTTOM / UTILITY.

:class:`RoleResolver` gives each player of the match exactly one role, per team:

1. the Riot ``position`` of the Live Client Data API when present (dominant score);
2. else Smite => JUNGLE (a player without Smite in a team that has one is not the jungler);
3. else inference from
   * the early-game minimap occupancy (visible time per lane / jungle between 1:30 and 5:00 of
     game time, accumulated from the :class:`~treeaicoach.tracker.Tracker`),
   * a built-in champion prior table (:data:`CHAMPION_ROLES`, all champions of the bundled
     index; unknown champions fall back on their Data Dragon tags),
   * summoner spells (Heal => BOTTOM, Exhaust => UTILITY, Teleport => TOP...).

The per-team assignment maximises the total score over all one-role-per-player permutations
(5! = 120: exact Hungarian-equivalent, trivially cheap). The result is exposed for the UI /
overlay (:meth:`RoleResolver.roles`) and for the gank logic (:meth:`RoleResolver.my_role`,
:meth:`RoleResolver.lane_opponents`, :meth:`RoleResolver.enemy_jungler`,
:meth:`RoleResolver.bot_lane`).

Thread safety: :meth:`update` runs on the analysis thread, accessors return immutable
snapshots. Never raises from its public methods. Pure Python (+ geometry), importable anywhere.
"""

from __future__ import annotations

import itertools
import logging
import math
import threading
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable

from treeaicoach.geometry import classify_zone, is_base, is_jungle, is_river, lane_of

if TYPE_CHECKING:  # pragma: no cover
    from treeaicoach.live_client import GameInfo, PlayerInfo
    from treeaicoach.tracker import Tracker

log = logging.getLogger(__name__)

TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY = "TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"
ROLES: tuple[str, ...] = (TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY)
#: Lane of each role (``None`` for the jungle).
ROLE_LANE: dict[str, str | None] = {TOP: "top", JUNGLE: None, MIDDLE: "mid", BOTTOM: "bot",
                                    UTILITY: "bot"}
#: Short French labels for the UI / overlay.
ROLE_LABEL_FR: dict[str, str] = {TOP: "Haut", JUNGLE: "Jungle", MIDDLE: "Milieu",
                                 BOTTOM: "Tireur", UTILITY: "Support"}
ROLE_SHORT: dict[str, str] = {TOP: "TOP", JUNGLE: "JGL", MIDDLE: "MID", BOTTOM: "ADC",
                              UTILITY: "SUP"}

_LETTER = {"T": TOP, "J": JUNGLE, "M": MIDDLE, "B": BOTTOM, "U": UTILITY}
_POSITION_ALIASES = {"TOP": TOP, "JUNGLE": JUNGLE, "MIDDLE": MIDDLE, "MID": MIDDLE,
                     "BOTTOM": BOTTOM, "BOT": BOTTOM, "ADC": BOTTOM, "UTILITY": UTILITY,
                     "SUPPORT": UTILITY}

#: Common roles of every champion, most frequent first (T top, J jungle, M mid, B bot, U support).
CHAMPION_ROLES: dict[str, str] = {
    "Aatrox": "T", "Ahri": "M", "Akali": "MT", "Akshan": "MT", "Alistar": "U", "Ambessa": "TJ",
    "Amumu": "JU", "Anivia": "M", "Annie": "MU", "Aphelios": "B", "Ashe": "BU",
    "AurelionSol": "M", "Aurora": "MT", "Azir": "M", "Bard": "U", "Belveth": "J",
    "Blitzcrank": "U", "Brand": "UMJ", "Braum": "U", "Briar": "J", "Caitlyn": "B",
    "Camille": "T", "Cassiopeia": "M", "Chogath": "TM", "Corki": "MB", "Darius": "T",
    "Diana": "JM", "DrMundo": "TJ", "Draven": "B", "Ekko": "JM", "Elise": "J", "Evelynn": "J",
    "Ezreal": "B", "Fiddlesticks": "J", "Fiora": "T", "Fizz": "M", "Galio": "MU",
    "Gangplank": "T", "Garen": "T", "Gnar": "T", "Gragas": "JT", "Graves": "J", "Gwen": "TJ",
    "Hecarim": "J", "Heimerdinger": "MTU", "Hwei": "MU", "Illaoi": "T", "Irelia": "TM",
    "Ivern": "J", "Janna": "U", "JarvanIV": "J", "Jax": "TJ", "Jayce": "TM", "Jhin": "B",
    "Jinx": "B", "KSante": "T", "Kaisa": "B", "Kalista": "B", "Karma": "UM", "Karthus": "JM",
    "Kassadin": "M", "Katarina": "M", "Kayle": "T", "Kayn": "J", "Kennen": "T", "Khazix": "J",
    "Kindred": "J", "Kled": "T", "KogMaw": "B", "Leblanc": "M", "LeeSin": "J", "Leona": "U",
    "Lillia": "J", "Lissandra": "M", "Locke": "MJ", "Lucian": "BM", "Lulu": "U", "Lux": "UM",
    "Malphite": "TM", "Malzahar": "M", "Maokai": "UJT", "MasterYi": "J", "Mel": "MU",
    "Milio": "U", "MissFortune": "B", "MonkeyKing": "JT", "Mordekaiser": "T", "Morgana": "UJ",
    "Naafiri": "MJ", "Nami": "U", "Nasus": "T", "Nautilus": "U", "Neeko": "MU", "Nidalee": "J",
    "Nilah": "B", "Nocturne": "J", "Nunu": "J", "Olaf": "TJ", "Orianna": "M", "Ornn": "T",
    "Pantheon": "TUM", "Poppy": "JTU", "Pyke": "U", "Qiyana": "MJ", "Quinn": "T", "Rakan": "U",
    "Rammus": "J", "RekSai": "J", "Rell": "U", "Renata": "U", "Renekton": "T", "Rengar": "JT",
    "Riven": "T", "Rumble": "TM", "Ryze": "MT", "Samira": "B", "Sejuani": "J", "Senna": "UB",
    "Seraphine": "UBM", "Sett": "TU", "Shaco": "JU", "Shen": "TU", "Shyvana": "J",
    "Singed": "T", "Sion": "T", "Sivir": "B", "Skarner": "J", "Smolder": "BM", "Sona": "U",
    "Soraka": "U", "Swain": "UMB", "Sylas": "MJ", "Syndra": "M", "TahmKench": "TU",
    "Taliyah": "JM", "Talon": "MJ", "Taric": "U", "Teemo": "T", "Thresh": "U",
    "Tristana": "BM", "Trundle": "JT", "Tryndamere": "T", "TwistedFate": "M", "Twitch": "BJ",
    "Udyr": "JT", "Urgot": "T", "Varus": "BM", "Vayne": "BT", "Veigar": "MB", "Velkoz": "UM",
    "Vex": "M", "Vi": "J", "Viego": "J", "Viktor": "M", "Vladimir": "MT", "Volibear": "TJ",
    "Warwick": "JT", "Xayah": "B", "Xerath": "UM", "XinZhao": "J", "Yasuo": "MT", "Yone": "MT",
    "Yorick": "T", "Yunara": "B", "Yuumi": "U", "Zaahen": "TJ", "Zac": "J", "Zed": "MJ",
    "Zeri": "B", "Ziggs": "BM", "Zilean": "UM", "Zoe": "M", "Zyra": "UJ",
}
#: Fallback from Data Dragon tags (first tag first) for champions missing from the table.
_TAG_ROLES: dict[str, str] = {"Marksman": "B", "Support": "U", "Mage": "M", "Assassin": "MJ",
                              "Fighter": "TJ", "Tank": "TU"}
_PRIOR_WEIGHTS = (1.0, 0.55, 0.3)
PRIOR_SCALE = 2.0

# Summoner spells: normalized keywords (English / French display names, raw ids) -> hints.
_SPELL_WORDS: dict[str, tuple[str, ...]] = {
    "heal": ("heal", "soin"),
    "exhaust": ("exhaust", "fatigue"),
    "teleport": ("teleport",),
    "ignite": ("ignite", "summonerdot", "embrasement"),
    "barrier": ("barrier", "barriere"),
    "ghost": ("summonerhaste", "ghost", "fantome"),
    "cleanse": ("summonerboost", "cleanse", "purification"),
    "smite": ("smite", "chatiment"),
}
SPELL_HINTS: dict[str, dict[str, float]] = {
    "heal": {BOTTOM: 1.5},
    "exhaust": {UTILITY: 1.0},
    "teleport": {TOP: 1.0, MIDDLE: 0.4},
    "ignite": {TOP: 0.3, MIDDLE: 0.3, UTILITY: 0.3},
    "barrier": {MIDDLE: 0.5, BOTTOM: 0.3},
    "ghost": {TOP: 0.3, BOTTOM: 0.3},
    "cleanse": {BOTTOM: 0.5},
}
RIOT_SCORE = 100.0
SMITE_SCORE = 50.0
NO_SMITE_PENALTY = 3.0

# Early-game occupancy (game time window, seconds) and weights.
OCC_START_GT = 90.0
OCC_END_GT = 300.0
OCC_MAX_DT = 0.5          # a tick counts for at most this long
OCC_FULL_S = 40.0         # observed time for full confidence
OCC_WEIGHTS: dict[str, dict[str, float]] = {
    "top": {TOP: 5.0}, "mid": {MIDDLE: 5.0}, "bot": {BOTTOM: 4.0, UTILITY: 4.0},
    "jungle": {JUNGLE: 5.0},
}
RECOMPUTE_EVERY_S = 1.0


def _norm(s: Any) -> str:
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if ch.isalnum() and not unicodedata.combining(ch)).casefold()


def normalize_role(position: Any) -> str | None:
    """Riot position / role word -> role constant, or None."""
    if not isinstance(position, str):
        return None
    return _POSITION_ALIASES.get(position.strip().upper())


def lane_opponent_roles(role: str | None) -> frozenset[str]:
    """Enemy roles facing ``role`` in lane (bot lane = BOTTOM + UTILITY pair; jungle: none)."""
    if role in (BOTTOM, UTILITY):
        return frozenset({BOTTOM, UTILITY})
    if role in (TOP, MIDDLE):
        return frozenset({role})
    return frozenset()


def champion_prior(alias: Any, tags: Iterable[str] | None = None) -> dict[str, float]:
    """Prior role scores of a champion (table, else Data Dragon tags, else flat)."""
    letters = CHAMPION_ROLES.get(alias) if isinstance(alias, str) else None
    if letters is None and isinstance(alias, str):
        key = _norm(alias)
        for name, lt in CHAMPION_ROLES.items():
            if _norm(name) == key:
                letters = lt
                break
    if letters is None:
        tag_list = list(tags) if tags is not None else _db_tags(alias)
        seq = ""
        for tag in tag_list:
            for ch in _TAG_ROLES.get(str(tag), ""):
                if ch not in seq:
                    seq += ch
        letters = seq
    out = {r: 0.0 for r in ROLES}
    for w, ch in zip(_PRIOR_WEIGHTS, letters or ""):
        role = _LETTER.get(ch)
        if role is not None:
            out[role] = max(out[role], w * PRIOR_SCALE)
    return out


def _db_tags(alias: Any) -> list[str]:
    if not isinstance(alias, str) or not alias:
        return []
    try:
        from treeaicoach.champions import get_default_db

        entry = get_default_db().get(alias)
        return list(entry.tags) if entry is not None else []
    except Exception:
        return []


def spell_kinds(player: Any) -> set[str]:
    """Summoner spell kinds (``"heal"``, ``"exhaust"``...) of a player, from any available field."""
    words: list[str] = []
    for attr in ("spells", "spell_ids"):
        val = getattr(player, attr, None)
        if isinstance(val, (list, tuple)):
            words.extend(_norm(x) for x in val if isinstance(x, str))
    kinds: set[str] = set()
    for w in words:
        if not w:
            continue
        for kind, keys in _SPELL_WORDS.items():
            if any(k in w for k in keys):
                kinds.add(kind)
    if bool(getattr(player, "has_smite", False)):
        kinds.add("smite")
    return kinds


def assign_roles(scores: list[dict[str, float]]) -> list[str | None]:
    """Best one-role-per-player assignment maximising the total score.

    Exact search over the permutations for up to 5 players (greedy beyond, extra players get
    ``None``). Ties are broken by the input order (deterministic).
    """
    n = len(scores)
    if n == 0:
        return []
    if n <= len(ROLES):
        best: tuple[float, tuple[str, ...]] | None = None
        for perm in itertools.permutations(ROLES, n):
            total = sum(scores[i].get(r, 0.0) for i, r in enumerate(perm))
            if best is None or total > best[0] + 1e-9:
                best = (total, perm)
        return list(best[1]) if best is not None else [None] * n
    out: list[str | None] = [None] * n
    cells = sorted(((s.get(r, 0.0), -i, r) for i, s in enumerate(scores) for r in ROLES),
                   reverse=True)
    used_p: set[int] = set()
    used_r: set[str] = set()
    for _v, ni, r in cells:
        i = -ni
        if i in used_p or r in used_r:
            continue
        out[i] = r
        used_p.add(i)
        used_r.add(r)
    return out


@dataclass(frozen=True)
class RoleInfo:
    """Role of one player."""

    alias: str
    team: str                 # "ORDER" | "CHAOS"
    side: str                 # "ally" (me included) | "enemy"
    role: str | None
    source: str               # "riot" | "smite" | "inferred"
    is_me: bool = False
    confidence: float = 0.0   # 0..1 (1 for riot / smite)

    @property
    def label_fr(self) -> str:
        return ROLE_LABEL_FR.get(self.role or "", "?")

    @property
    def short(self) -> str:
        return ROLE_SHORT.get(self.role or "", "?")


def _zone_class(u: float, v: float) -> str | None:
    z = classify_zone(u, v)
    if is_base(z):
        return None
    if is_jungle(z) or is_river(z):
        return "jungle"
    return lane_of(z)


class RoleResolver:
    """Assigns one role per player and team. See the module docstring."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._occ: dict[tuple[str, str], dict[str, float]] = {}   # (alias key, side) -> class -> s
        self._roles: dict[tuple[str, str], RoleInfo] = {}
        self._signature: tuple | None = None
        self._last_t: float | None = None
        self._last_compute: float = -math.inf
        self._my_key: tuple[str, str] | None = None

    # -- public API ---------------------------------------------------------------------

    def reset(self) -> None:
        with self._lock:
            self._occ.clear()
            self._roles = {}
            self._signature = None
            self._last_t = None
            self._last_compute = -math.inf
            self._my_key = None

    def update(self, t: float, tracker: Tracker | None, game: GameInfo | None) -> None:
        """Accumulate the early-game occupancy and refresh the assignment. Never raises."""
        try:
            with self._lock:
                self._update_locked(t, tracker, game)
        except Exception:
            log.exception("RoleResolver.update failed")

    def roles(self) -> dict[str, RoleInfo]:
        """``alias -> RoleInfo`` for every player (mirror match: enemy key ``alias~enemy``)."""
        with self._lock:
            out: dict[str, RoleInfo] = {}
            for (_k, side), info in self._roles.items():
                key = info.alias if info.alias not in out else f"{info.alias}~{side}"
                out[key] = info
            return out

    def role_of(self, alias: Any, side: str | None = None) -> str | None:
        info = self.info(alias, side)
        return info.role if info is not None else None

    def info(self, alias: Any, side: str | None = None) -> RoleInfo | None:
        k = _norm(alias)
        if not k:
            return None
        with self._lock:
            if side is not None:
                return self._roles.get((k, side))
            return self._roles.get((k, "enemy")) or self._roles.get((k, "ally"))

    def me(self) -> RoleInfo | None:
        with self._lock:
            return self._roles.get(self._my_key) if self._my_key is not None else None

    def my_role(self) -> str | None:
        info = self.me()
        return info.role if info is not None else None

    def enemy_jungler(self) -> str | None:
        """Alias of the enemy assigned JUNGLE (None if unknown)."""
        with self._lock:
            for (_k, side), info in self._roles.items():
                if side == "enemy" and info.role == JUNGLE:
                    return info.alias
        return None

    def lane_opponents(self) -> frozenset[str]:
        """Aliases of the enemies facing me in lane (bot lane: the BOTTOM + UTILITY pair)."""
        roles = lane_opponent_roles(self.my_role())
        if not roles:
            return frozenset()
        with self._lock:
            return frozenset(info.alias for (_k, side), info in self._roles.items()
                             if side == "enemy" and info.role in roles)

    def bot_lane(self, side: str = "enemy") -> tuple[str, ...]:
        """Aliases of the BOTTOM + UTILITY pair of ``side`` ("ally" or "enemy")."""
        with self._lock:
            pair = sorted((info.role or "", info.alias) for (_k, s), info in self._roles.items()
                          if s == side and info.role in (BOTTOM, UTILITY))
        return tuple(a for _r, a in pair)

    # -- internals ----------------------------------------------------------------------

    def _players(self, game: GameInfo) -> list[tuple[PlayerInfo, str, bool]]:
        out: list[tuple[PlayerInfo, str, bool]] = []
        me = getattr(game, "me", None)
        if me is not None:
            out.append((me, "ally", True))
        for p in getattr(game, "allies", None) or ():
            out.append((p, "ally", False))
        for p in getattr(game, "enemies", None) or ():
            out.append((p, "enemy", False))
        return out

    def _update_locked(self, t: Any, tracker: Any, game: Any) -> None:
        try:
            now = float(t)
        except (TypeError, ValueError):
            return
        if not math.isfinite(now):
            return
        if self._last_t is not None and now < self._last_t - 1.0:
            self.reset()
        dt = 0.0 if self._last_t is None else min(max(0.0, now - self._last_t), OCC_MAX_DT)
        self._last_t = now
        if game is None:
            return
        gt = _as_float(getattr(game, "game_time", None))
        if tracker is not None and gt is not None and OCC_START_GT <= gt <= OCC_END_GT and dt > 0:
            self._accumulate(tracker, game, dt)
        sig = tuple((_norm(getattr(p, "champion_alias", "")), side, getattr(p, "position", ""),
                     bool(getattr(p, "has_smite", False)), tuple(getattr(p, "spells", ()) or ()))
                    for p, side, _me in self._players(game))
        if sig != self._signature or now - self._last_compute >= RECOMPUTE_EVERY_S:
            self._signature = sig
            self._last_compute = now
            self._compute(game)

    def _accumulate(self, tracker: Any, game: Any, dt: float) -> None:
        me_alias = _norm(getattr(getattr(game, "me", None), "champion_alias", ""))
        try:
            tracks = tracker.tracks()
        except Exception:
            return
        for tr in tracks:
            if not getattr(tr, "visible", False):
                continue
            relation = getattr(tr, "relation", "")
            side = "enemy" if relation == "enemy" else "ally"
            alias = _norm(getattr(tr, "alias", None))
            if relation == "self" and me_alias:
                alias = me_alias
            if not alias:
                continue
            pos = tr.position()
            if pos is None:
                continue
            cls = _zone_class(pos[0], pos[1])
            if cls is None:
                continue
            d = self._occ.setdefault((alias, side), {})
            d[cls] = d.get(cls, 0.0) + dt

    def _scores(self, p: Any, side: str, team_has_smite: bool) -> tuple[dict[str, float], str, float]:
        alias = getattr(p, "champion_alias", "") or getattr(p, "champion_name", "")
        scores = champion_prior(alias)
        source, conf = "inferred", 0.0
        riot = normalize_role(getattr(p, "position", None))
        kinds = spell_kinds(p)
        for kind in kinds:
            for role, w in SPELL_HINTS.get(kind, {}).items():
                scores[role] += w
        if "smite" in kinds:
            scores[JUNGLE] += SMITE_SCORE
            source, conf = "smite", 1.0
        elif team_has_smite:
            scores[JUNGLE] -= NO_SMITE_PENALTY
        occ = self._occ.get((_norm(alias), side))
        if occ:
            total = sum(occ.values())
            if total > 0:
                c = min(1.0, total / OCC_FULL_S)
                for cls, secs in occ.items():
                    for role, w in OCC_WEIGHTS.get(cls, {}).items():
                        scores[role] += w * c * secs / total
                if source == "inferred":
                    conf = 0.3 + 0.5 * c
        if riot is not None:
            scores[riot] += RIOT_SCORE
            source, conf = "riot", 1.0
        if source == "inferred" and conf == 0.0:
            conf = 0.3
        return scores, source, conf

    def _compute(self, game: Any) -> None:
        players = self._players(game)
        out: dict[tuple[str, str], RoleInfo] = {}
        my_key = None
        for side in ("ally", "enemy"):
            team_players = [(p, me) for p, s, me in players if s == side]
            if not team_players:
                continue
            smite = any(bool(getattr(p, "has_smite", False)) for p, _me in team_players)
            rows = [self._scores(p, side, smite) for p, _me in team_players]
            assigned = assign_roles([r[0] for r in rows])
            for (p, is_me), (_sc, source, conf), role in zip(team_players, rows, assigned):
                alias = getattr(p, "champion_alias", "") or getattr(p, "champion_name", "")
                k = _norm(alias)
                if not k:
                    continue
                riot = normalize_role(getattr(p, "position", None))
                if source == "riot" and riot != role:
                    source, conf = "inferred", 0.5          # conflicting Riot positions
                info = RoleInfo(alias=alias, team=str(getattr(p, "team", "") or ""), side=side,
                                role=role, source=source, is_me=is_me, confidence=conf)
                out[(k, side)] = info
                if is_me:
                    my_key = (k, side)
        self._roles = out
        self._my_key = my_key


def _as_float(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


__all__ = ["ROLES", "TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY", "ROLE_LANE", "ROLE_LABEL_FR",
           "ROLE_SHORT", "CHAMPION_ROLES", "RoleInfo", "RoleResolver", "assign_roles",
           "champion_prior", "lane_opponent_roles", "normalize_role", "spell_kinds"]
