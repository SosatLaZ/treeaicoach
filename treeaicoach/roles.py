"""Role of every player (both teams): TOP / JUNGLE / MIDDLE / BOTTOM / UTILITY.

:class:`RoleResolver` gives each player of the match exactly one role, per team:

0. **observed lane** (role swaps): Riot's ``position`` is the role ASSIGNED in champ select, not
   where people actually play. From 1:30 to 10:00 of game time every player's lane occupancy is
   accumulated (me from my own track, the others from their sightings, time-decayed so a roam
   does not count much); a player seen consistently in one lane (>= 60 % of the lane time over
   >= 45 s, or >= 20 s at >= 75 % before 5:00), confirmed for :data:`OBS_CONFIRM_S`, gets that
   lane's role(s) with a score above the Riot position (:data:`OBSERVED_SCORE`). Sticky: the
   committed lane only changes when another lane passes the same test (no flapping on roams);
1. the Riot ``position`` of the Live Client Data API when present (dominant score), unless the
   champions + summoner spells of two players strongly say they swapped (``Heal`` on the "mid",
   ``Teleport`` on the "ADC"...) and the map has not decided yet;
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
OCC_END_GT = 600.0        # lanes are observed from 1:30 to 10:00 (then roams / rotations)
OCC_MAX_DT = 0.5          # a tick counts for at most this long
OCC_FULL_S = 40.0         # observed time for full confidence
OCC_WEIGHTS: dict[str, dict[str, float]] = {
    "top": {TOP: 5.0}, "mid": {MIDDLE: 5.0}, "bot": {BOTTOM: 4.0, UTILITY: 4.0},
    "jungle": {JUNGLE: 5.0},
}
RECOMPUTE_EVERY_S = 1.0

# Observed lane (role swaps)
OBS_HALF_LIFE_S = 150.0   # lane occupancy decays with this half-life (a roam fades away)
OBS_MIN_TOTAL_S = 45.0    # raw observed lane time needed for the regular test
OBS_MIN_FRAC = 0.60       # share of the (decayed) lane time in the lane
OBS_EARLY_GT = 300.0      # before 5:00 ...
OBS_EARLY_LANE_S = 20.0   # ... 20 s in one lane ...
OBS_EARLY_FRAC = 0.75     # ... at >= 75 % is enough
OBS_LANE_SHARE = 0.40     # lane time must be >= 40 % of all the observed time (not a jungler)
OBS_CONFIRM_S = 8.0       # a new observed lane must hold this long (game time) before it counts
OBSERVED_SCORE = 250.0    # > RIOT_SCORE (and > 2 x RIOT_SCORE - RIOT_SCORE: beats one unobserved player)
LANE_ROLES: dict[str, tuple[str, ...]] = {"top": (TOP,), "mid": (MIDDLE,), "bot": (BOTTOM, UTILITY)}
PRIOR_SWAP_GAIN = 3.5     # champion priors + spells gain needed to swap two Riot positions


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
    assigned: str | None = None   # Riot position (champ select), None when absent
    lane_seen: str | None = None  # observed lane ("top" / "mid" / "bot"), None when not decided

    @property
    def swapped(self) -> bool:
        """True when the role differs from the champ select position (lane swap)."""
        return self.assigned is not None and self.role is not None and self.role != self.assigned

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
        self._obs: dict[tuple[str, str], _Obs] = {}
        self._gt: float | None = None
        self._my_swap: tuple[str, float] | None = None     # (role, wall time of the change)
        #: Optional ``() -> "top" | "mid" | "bot" | None``: MY observed lane from another source
        #: (engine.my_observed_lane: the learned minimap icon, custom skins). Used while the
        #: tracker-based observation of me has not decided.
        self.my_lane_hook: Any = None

    # -- public API ---------------------------------------------------------------------

    def reset(self) -> None:
        with self._lock:
            self._occ.clear()
            self._roles = {}
            self._signature = None
            self._last_t = None
            self._last_compute = -math.inf
            self._my_key = None
            self._obs = {}
            self._gt = None
            self._my_swap = None

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

    def my_swap(self) -> tuple[str, float] | None:
        """``(role, t)`` when my role differs from my champ select position (``t`` = when it was
        detected, the clock of :meth:`update`), else None."""
        with self._lock:
            info = self._roles.get(self._my_key) if self._my_key is not None else None
            if info is None or not info.swapped:
                return None
            return self._my_swap

    def observed_lane(self, alias: Any, side: str = "enemy") -> str | None:
        """Lane where ``alias`` was seen laning (committed), or None."""
        with self._lock:
            ob = self._obs.get((_norm(alias), side))
            return ob.lane if ob is not None else None

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
        self._gt = gt
        changed = False
        if tracker is not None and gt is not None and OCC_START_GT <= gt <= OCC_END_GT and dt > 0:
            self._accumulate(tracker, game, dt)
            changed = self._decide_lanes(gt)
        changed = self._apply_my_lane_hook(game) or changed
        sig = tuple((_norm(getattr(p, "champion_alias", "")), side, getattr(p, "position", ""),
                     bool(getattr(p, "has_smite", False)), tuple(getattr(p, "spells", ()) or ()))
                    for p, side, _me in self._players(game))
        if changed or sig != self._signature or now - self._last_compute >= RECOMPUTE_EVERY_S:
            self._signature = sig
            self._last_compute = now
            self._compute(game, now)

    def _accumulate(self, tracker: Any, game: Any, dt: float) -> None:
        me_alias = _norm(getattr(getattr(game, "me", None), "champion_alias", ""))
        try:
            tracks = tracker.tracks()
        except Exception:
            return
        decay = 0.5 ** (dt / OBS_HALF_LIFE_S)
        for ob in self._obs.values():
            for cls in ob.dec:
                ob.dec[cls] *= decay
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
            ob = self._obs.setdefault((alias, side), _Obs())
            ob.raw[cls] = ob.raw.get(cls, 0.0) + dt
            ob.dec[cls] = ob.dec.get(cls, 0.0) + dt

    def _apply_my_lane_hook(self, game: Any) -> bool:
        """My lane from :attr:`my_lane_hook` when my own observation is undecided."""
        hook = self.my_lane_hook
        if hook is None:
            return False
        try:
            lane = hook()
        except Exception:
            return False
        me = _norm(getattr(getattr(game, "me", None), "champion_alias", ""))
        if lane not in LANE_ROLES or not me:
            return False
        ob = self._obs.setdefault((me, "ally"), _Obs())
        if ob.lane is not None:
            return False
        ob.lane = lane
        return True

    def _decide_lanes(self, gt: float) -> bool:
        """Commit / change every player's observed lane (with confirmation). True on a change."""
        changed = False
        for ob in self._obs.values():
            cand = ob.candidate(gt)
            if cand is None or cand == ob.lane:
                ob.cand, ob.cand_since = None, None
                continue
            if cand != ob.cand or ob.cand_since is None or gt < ob.cand_since:
                ob.cand, ob.cand_since = cand, gt
                continue
            if gt - ob.cand_since >= OBS_CONFIRM_S:
                ob.lane, ob.cand, ob.cand_since = cand, None, None
                changed = True
        return changed

    def _evidence(self, p: Any) -> dict[str, float]:
        """Champion prior + summoner spell hints (no Riot position, no map)."""
        alias = getattr(p, "champion_alias", "") or getattr(p, "champion_name", "")
        scores = champion_prior(alias)
        for kind in spell_kinds(p):
            for role, w in SPELL_HINTS.get(kind, {}).items():
                scores[role] += w
        return scores

    def _prior_swaps(self, team: list[tuple[Any, bool]], side: str) -> dict[int, str]:
        """Index -> corrected Riot position when two players' champions + spells say they swapped
        lanes (only while the map has not decided their lane)."""
        riot = [normalize_role(getattr(p, "position", None)) for p, _me in team]
        ev = [self._evidence(p) for p, _me in team]
        free = []
        for i, (p, _me) in enumerate(team):
            ob = self._obs.get((_norm(getattr(p, "champion_alias", "") or getattr(p, "champion_name", "")), side))
            ok = (riot[i] not in (None, JUNGLE) and not bool(getattr(p, "has_smite", False))
                  and (ob is None or ob.lane is None))
            free.append(ok)
        best: tuple[float, int, int] | None = None
        for i in range(len(team)):
            for j in range(i + 1, len(team)):
                if not (free[i] and free[j]) or riot[i] == riot[j]:
                    continue
                ri, rj = riot[i], riot[j]
                if ROLE_LANE.get(ri or "") == ROLE_LANE.get(rj or ""):
                    continue                              # ADC <-> support: not a lane swap
                gain = ev[i][rj] + ev[j][ri] - ev[i][ri] - ev[j][rj]
                if gain >= PRIOR_SWAP_GAIN and (best is None or gain > best[0]):
                    best = (gain, i, j)
        if best is None:
            return {}
        _g, i, j = best
        return {i: riot[j], j: riot[i]}                  # type: ignore[dict-item]

    def _scores(self, p: Any, side: str, team_has_smite: bool,
                riot_fix: str | None = None) -> tuple[dict[str, float], str, float]:
        alias = getattr(p, "champion_alias", "") or getattr(p, "champion_name", "")
        scores = champion_prior(alias)
        source, conf = "inferred", 0.0
        riot = riot_fix or normalize_role(getattr(p, "position", None))
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
            source, conf = ("inferred", 0.6) if riot_fix else ("riot", 1.0)
        ob = self._obs.get((_norm(alias), side))
        if ob is not None and ob.lane in LANE_ROLES and "smite" not in kinds:
            for role in LANE_ROLES[ob.lane]:
                scores[role] += OBSERVED_SCORE
        if source == "inferred" and conf == 0.0:
            conf = 0.3
        return scores, source, conf

    def _compute(self, game: Any, now: float = 0.0) -> None:
        players = self._players(game)
        if not players:
            return                                   # nothing usable: keep the last result
        out: dict[tuple[str, str], RoleInfo] = {}
        my_key = None
        for side in ("ally", "enemy"):
            team_players = [(p, me) for p, s, me in players if s == side]
            if not team_players:
                continue
            smite = any(bool(getattr(p, "has_smite", False)) for p, _me in team_players)
            fixes = self._prior_swaps(team_players, side)
            rows = [self._scores(p, side, smite, fixes.get(i)) for i, (p, _me) in enumerate(team_players)]
            assigned = assign_roles([r[0] for r in rows])
            for (p, is_me), (_sc, source, conf), role in zip(team_players, rows, assigned):
                alias = getattr(p, "champion_alias", "") or getattr(p, "champion_name", "")
                k = _norm(alias)
                if not k:
                    continue
                riot = normalize_role(getattr(p, "position", None))
                ob = self._obs.get((k, side))
                lane = ob.lane if ob is not None else None
                if source != "smite" and lane is not None and ROLE_LANE.get(role or "") == lane \
                        and (riot is None or ROLE_LANE.get(riot) != lane):
                    source, conf = "observed", 0.9          # seen laning there (lane swap)
                elif source == "riot" and riot != role:
                    source, conf = "inferred", 0.5          # conflicting Riot positions
                info = RoleInfo(alias=alias, team=str(getattr(p, "team", "") or ""), side=side,
                                role=role, source=source, is_me=is_me, confidence=conf,
                                assigned=riot, lane_seen=lane)
                out[(k, side)] = info
                if is_me:
                    my_key = (k, side)
        old = self._roles.get(self._my_key) if self._my_key is not None else None
        new = out.get(my_key) if my_key is not None else None
        if new is not None and new.swapped and (old is None or old.role != new.role or self._my_swap is None):
            self._my_swap = (new.role or "", now)
            log.info("Role swap detected: I play %s (assigned %s, source %s)", new.role, new.assigned,
                     new.source)
        elif new is None or not new.swapped:
            self._my_swap = None
        self._roles = out
        self._my_key = my_key


class _Obs:
    """Lane occupancy of one player (raw + time-decayed seconds per class) + committed lane."""

    __slots__ = ("raw", "dec", "lane", "cand", "cand_since")

    def __init__(self) -> None:
        self.raw: dict[str, float] = {}
        self.dec: dict[str, float] = {}
        self.lane: str | None = None
        self.cand: str | None = None
        self.cand_since: float | None = None

    def candidate(self, gt: float) -> str | None:
        """Lane passing the observed-lane test now (see the module docstring), or None."""
        lanes = {c: self.dec.get(c, 0.0) for c in LANE_ROLES}
        lane_dec = sum(lanes.values())
        all_dec = sum(self.dec.values())
        if lane_dec <= 1e-6 or lane_dec < OBS_LANE_SHARE * all_dec:
            return None
        best = max(lanes, key=lambda c: lanes[c])
        frac = lanes[best] / lane_dec
        raw_lane = sum(self.raw.get(c, 0.0) for c in LANE_ROLES)
        if raw_lane >= OBS_MIN_TOTAL_S and frac >= OBS_MIN_FRAC:
            return best
        if gt <= OBS_EARLY_GT and self.raw.get(best, 0.0) >= OBS_EARLY_LANE_S and frac >= OBS_EARLY_FRAC:
            return best
        return None


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
