"""Riot's official Live Client Data API (``https://127.0.0.1:2999``): parsing + HTTP client.

The game itself serves this API on the player's machine while a match is running. We only
read ``/liveclientdata/allgamedata`` (roster, teams, positions, summoner spells, game time,
events). :func:`parse_allgamedata` is pure (no network) and never raises;
:class:`LiveClient` performs the request with ``urllib`` (no proxy, certificate check
disabled only for the loopback host, whose certificate is self-signed by Riot).
"""

from __future__ import annotations

import http.client
import json
import logging
import math
import ssl
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from treeaicoach.champions import alias_from_raw, normalize_name

log = logging.getLogger(__name__)

LIVE_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
TEAMS = ("ORDER", "CHAOS")
POSITIONS = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
_POSITION_ALIASES = {
    "TOP": "TOP", "JUNGLE": "JUNGLE", "JUNGLER": "JUNGLE", "JGL": "JUNGLE",
    "MIDDLE": "MIDDLE", "MID": "MIDDLE", "BOTTOM": "BOTTOM", "BOT": "BOTTOM", "ADC": "BOTTOM",
    "CARRY": "BOTTOM", "UTILITY": "UTILITY", "SUPPORT": "UTILITY", "SUP": "UTILITY",
}
#: Normalized (accent-less, case-folded, alphanumeric) display names of Smite and its upgrades.
_SMITE_WORDS = ("smite", "chatiment")
SUMMONERS_RIFT_MAP_NUMBER = 11
#: Keys of ``PlayerInfo.scores`` (same names as the API's ``scores`` object).
ZERO_SCORES: dict[str, float] = {"kills": 0, "deaths": 0, "assists": 0, "creepScore": 0, "wardScore": 0.0}
_warned_unmatched = False


@dataclass
class PlayerInfo:
    """One player of the match, as reported by the Live Client Data API."""

    riot_id: str = ""
    summoner_name: str = ""
    champion_alias: str = ""       # "MonkeyKing" (from rawChampionName)
    champion_name: str = ""        # localized name ("Wukong")
    team: str = ""                 # "ORDER" | "CHAOS"
    position: str = ""             # "TOP" | "JUNGLE" | "MIDDLE" | "BOTTOM" | "UTILITY" | ""
    is_dead: bool = False
    respawn_timer: float = 0.0
    level: int = 1
    skin_id: int = 0
    has_smite: bool = False
    is_bot: bool = False
    spells: tuple[str, ...] = ()   # summoner spells (localized display names)
    spell_ids: tuple[str, ...] = ()  # language-independent ids ("SummonerHeal"), when known
    items: list[int] = field(default_factory=list)            # itemIDs, by inventory slot
    scores: dict[str, float] = field(default_factory=lambda: dict(ZERO_SCORES))
    current_gold: float = 0.0      # only known for me (activePlayer.currentGold)

    @property
    def kills(self) -> int:
        return int(self.scores.get("kills", 0))

    @property
    def deaths(self) -> int:
        return int(self.scores.get("deaths", 0))

    @property
    def assists(self) -> int:
        return int(self.scores.get("assists", 0))

    @property
    def creep_score(self) -> int:
        return int(self.scores.get("creepScore", 0))

    @property
    def ward_score(self) -> float:
        return float(self.scores.get("wardScore", 0.0))


@dataclass
class GameInfo:
    """Snapshot of the match (one API poll)."""

    game_time: float = 0.0
    game_mode: str = ""
    map_number: int = 0
    map_terrain: str = "Default"
    team_relative_colors: bool = True
    me: PlayerInfo | None = None               # None when spectating
    allies: list[PlayerInfo] = field(default_factory=list)    # without me (ORDER when spectating)
    enemies: list[PlayerInfo] = field(default_factory=list)   # (CHAOS when spectating)
    events: list[dict] = field(default_factory=list)
    fetched_at: float = field(default_factory=time.monotonic)
    current_gold: float = 0.0                  # my gold (activePlayer.currentGold), 0 when spectating
    #: my numeric ``activePlayer.championStats`` (currentHealth, maxHealth...), {} when spectating
    champion_stats: dict[str, float] = field(default_factory=dict)

    @property
    def items(self) -> list[int]:
        """My itemIDs (empty when spectating)."""
        return list(self.me.items) if self.me is not None else []

    @property
    def scores(self) -> dict[str, float]:
        """My scores: kills, deaths, assists, creepScore, wardScore (zeros when spectating)."""
        return dict(self.me.scores) if self.me is not None else dict(ZERO_SCORES)

    @property
    def game_result(self) -> str | None:
        """``"Win"`` / ``"Lose"`` once the ``GameEnd`` event is present, else ``None``."""
        for e in reversed(self.events):
            if isinstance(e, dict) and e.get("EventName") == "GameEnd":
                res = _str(e.get("Result")).strip().casefold()
                return {"win": "Win", "lose": "Lose", "loss": "Lose", "defeat": "Lose",
                        "victory": "Win"}.get(res)
        return None

    def enemy_jungler(self) -> PlayerInfo | None:
        """Enemy with Smite (the JUNGLE one if several), else the enemy in position JUNGLE."""
        smiters = [p for p in self.enemies if p.has_smite]
        if smiters:
            for p in smiters:
                if p.position == "JUNGLE":
                    return p
            return smiters[0]
        for p in self.enemies:
            if p.position == "JUNGLE":
                return p
        return None

    def player_by_alias(self, alias: str) -> PlayerInfo | None:
        """Player playing ``alias`` (case / punctuation insensitive; raw names accepted)."""
        key = normalize_name(alias_from_raw(alias)) if alias else ""
        if not key:
            return None
        for p in self.all_players():
            if normalize_name(p.champion_alias) == key:
                return p
        for p in self.all_players():   # localized name as a last resort
            if normalize_name(p.champion_name) == key:
                return p
        return None

    def all_players(self) -> list[PlayerInfo]:
        """Me (if any), then allies, then enemies."""
        return ([self.me] if self.me is not None else []) + list(self.allies) + list(self.enemies)

    @property
    def is_summoners_rift(self) -> bool:
        """True on Summoner's Rift (map 11)."""
        return self.map_number == SUMMONERS_RIFT_MAP_NUMBER

    @property
    def is_spectator(self) -> bool:
        """True when no local player could be identified (spectator / replay)."""
        return self.me is None

    @property
    def my_team(self) -> str | None:
        """``"ORDER"`` / ``"CHAOS"`` of the local player, ``None`` when spectating."""
        return self.me.team if self.me is not None else None

    @property
    def enemy_team(self) -> str:
        """Team of :attr:`enemies`."""
        if self.me is None:
            return "CHAOS"
        return "CHAOS" if self.me.team == "ORDER" else "ORDER"


# ---------------------------------------------------------------------------- helpers
def _str(x: Any) -> str:
    if x is None or isinstance(x, (dict, list)):
        return ""
    return str(x).strip()


def _float(x: Any, default: float = 0.0) -> float:
    try:
        f = float(x)
    except (TypeError, ValueError, OverflowError):
        return default
    return f if math.isfinite(f) else default


def _int(x: Any, default: int = 0) -> int:
    if isinstance(x, bool):
        return int(x)
    try:
        return int(x)
    except (TypeError, ValueError, OverflowError):
        f = _float(x, float("nan"))
        return int(f) if math.isfinite(f) else default


def _bool(x: Any, default: bool = False) -> bool:
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return bool(x) if math.isfinite(float(x)) else default
    if isinstance(x, str):
        s = x.strip().lower()
        if s in ("true", "1", "yes", "oui"):
            return True
        if s in ("false", "0", "no", "non", ""):
            return False
    return default


def _norm_id(x: Any) -> str:
    """Comparable form of a Riot ID / summoner name."""
    s = _str(x)
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s).replace("\u00a0", " ").replace("\u200b", "")
    return " ".join(s.split()).casefold()


def _riot_id(d: dict) -> str:
    rid = _str(d.get("riotId"))
    if rid:
        return rid
    name, tag = _str(d.get("riotIdGameName")), _str(d.get("riotIdTagLine"))
    if name and tag:
        return f"{name}#{tag}"
    return ""


def _game_name(d: dict) -> str:
    name = _str(d.get("riotIdGameName"))
    if name:
        return name
    for k in ("riotId", "summonerName"):
        v = _str(d.get(k))
        if v:
            return v.split("#", 1)[0]
    return ""


def _normalize_position(x: Any) -> str:
    return _POSITION_ALIASES.get(_str(x).upper(), "")


def _spell_is_smite(spell: Any) -> bool:
    if not isinstance(spell, dict):
        return False
    raw = _str(spell.get("rawDisplayName")).casefold()
    if "smite" in raw:
        return True
    disp = normalize_name(spell.get("displayName"))
    return any(w in disp for w in _SMITE_WORDS)


def _spell_id(spell: Any) -> str:
    """``"SummonerHeal"`` from ``rawDisplayName`` ("GeneratedTip_SummonerSpell_SummonerHeal_DisplayName")."""
    if not isinstance(spell, dict):
        return ""
    raw = _str(spell.get("rawDisplayName"))
    for part in raw.split("_"):
        if part.startswith("Summoner") and part != "SummonerSpell":
            return part
    return ""


def _resolve_alias(raw_champion: Any, champion_name: str) -> str:
    """Champion alias from rawChampionName, canonicalized with the bundled index if possible."""
    alias = alias_from_raw(_str(raw_champion))
    try:
        from treeaicoach.champions import get_default_db

        db = get_default_db()
        entry = db.get(alias) if alias else None
        if entry is None and champion_name:
            entry = db.get(champion_name)
        if entry is not None:
            return entry.alias
    except Exception:
        log.debug("Champion index unavailable for alias resolution", exc_info=True)
    if alias:
        return alias
    return "".join(ch for ch in champion_name if ch.isalnum())


def _parse_player(d: dict) -> PlayerInfo | None:
    team = _str(d.get("team")).upper()
    if team not in TEAMS:
        return None
    spells_raw = d.get("summonerSpells")
    spell_list: list[dict] = []
    if isinstance(spells_raw, dict):
        for k in ("summonerSpellOne", "summonerSpellTwo"):
            s = spells_raw.get(k)
            if isinstance(s, dict):
                spell_list.append(s)
    champion_name = _str(d.get("championName"))
    riot_id = _riot_id(d)
    summoner = _str(d.get("summonerName"))
    return PlayerInfo(
        riot_id=riot_id or summoner,
        summoner_name=summoner or riot_id,
        champion_alias=_resolve_alias(d.get("rawChampionName"), champion_name),
        champion_name=champion_name,
        team=team,
        position=_normalize_position(d.get("position")),
        is_dead=_bool(d.get("isDead"), False),
        respawn_timer=max(0.0, _float(d.get("respawnTimer"), 0.0)),
        level=max(1, _int(d.get("level"), 1)),
        skin_id=max(0, _int(d.get("skinID"), 0)),
        has_smite=any(_spell_is_smite(s) for s in spell_list),
        is_bot=_bool(d.get("isBot"), False),
        spells=tuple(_str(s.get("displayName")) for s in spell_list),
        spell_ids=tuple(i for i in (_spell_id(s) for s in spell_list) if i),
        items=_parse_items(d.get("items")),
        scores=_parse_scores(d.get("scores")),
    )


def _parse_items(items: Any) -> list[int]:
    """itemIDs sorted by inventory slot (invalid entries skipped)."""
    if not isinstance(items, list):
        return []
    found: list[tuple[int, int]] = []
    for n, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        item_id = _int(it.get("itemID"), 0)
        if item_id > 0:
            found.append((_int(it.get("slot"), 100 + n), item_id))
    return [item_id for _, item_id in sorted(found, key=lambda x: x[0])]


def _parse_scores(scores: Any) -> dict[str, float]:
    out = dict(ZERO_SCORES)
    if isinstance(scores, dict):
        for k in ("kills", "deaths", "assists", "creepScore"):
            out[k] = max(0, _int(scores.get(k), 0))
        out["wardScore"] = max(0.0, _float(scores.get("wardScore"), 0.0))
    return out


def _champion_stats(active: dict | None) -> dict[str, float]:
    """Numeric fields of ``activePlayer.championStats`` (finite floats only)."""
    stats = active.get("championStats") if isinstance(active, dict) else None
    if not isinstance(stats, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in stats.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
            out[str(k)] = float(v)
    return out


def _find_me(active: dict | None, raws: list[dict]) -> int | None:
    """Index in ``raws`` of the local player: riotId, then summonerName, then game name."""
    if not active or not raws:
        return None
    criteria = (
        lambda d: _norm_id(_riot_id(d)),
        lambda d: _norm_id(d.get("summonerName")),
        lambda d: _norm_id(d.get("riotIdGameName")),
        lambda d: _norm_id(_game_name(d)),
    )
    for crit in criteria:
        target = crit(active)
        if not target:
            continue
        matches = [i for i, d in enumerate(raws) if crit(d) == target]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            log.debug("Ambiguous local player match on %r", target)
    return None


def _map_number(gd: dict) -> int:
    n = _int(gd.get("mapNumber"), 0)
    if n:
        return n
    name = _str(gd.get("mapName"))
    digits = "".join(ch for ch in name if ch.isdigit())
    return _int(digits, 0) if digits else 0


def _parse(data: Any, now: float | None) -> GameInfo | None:
    if not isinstance(data, dict):
        return None
    if "errorCode" in data or ("httpStatus" in data and "gameData" not in data):
        return None
    gd = data.get("gameData")
    if not isinstance(gd, dict) or not gd:
        return None
    raw_players = data.get("allPlayers")
    if not isinstance(raw_players, list):
        return None
    active = data.get("activePlayer")
    if not isinstance(active, dict) or "error" in active or not active:
        active = None

    parsed: list[tuple[dict, PlayerInfo]] = []
    for d in raw_players:
        if not isinstance(d, dict):
            continue
        p = _parse_player(d)
        if p is not None:
            parsed.append((d, p))
    if not parsed:
        return None   # loading screen / broken payload: no usable roster

    me_idx = _find_me(active, [d for d, _ in parsed])
    me = parsed[me_idx][1] if me_idx is not None else None
    current_gold = 0.0
    if me is not None and active is not None:
        current_gold = max(0.0, _float(active.get("currentGold"), 0.0))
        me.current_gold = current_gold
    if active is not None and me is None:
        global _warned_unmatched
        if not _warned_unmatched:
            _warned_unmatched = True
            log.warning("Local player (activePlayer) not found in allPlayers; using ORDER as allies")
    players = [p for _, p in parsed]
    if me is not None:
        allies = [p for p in players if p is not me and p.team == me.team]
        enemies = [p for p in players if p.team != me.team]
    else:
        allies = [p for p in players if p.team == "ORDER"]
        enemies = [p for p in players if p.team == "CHAOS"]

    events: list[dict] = []
    ev = data.get("events")
    if isinstance(ev, dict):
        lst = ev.get("Events")
        if isinstance(lst, list):
            events = [e for e in lst if isinstance(e, dict)]

    t_now = _float(now, math.nan) if now is not None else math.nan
    return GameInfo(
        game_time=max(0.0, _float(gd.get("gameTime"), 0.0)),
        game_mode=_str(gd.get("gameMode")).upper(),
        map_number=_map_number(gd),
        map_terrain=_str(gd.get("mapTerrain")) or "Default",
        team_relative_colors=_bool(active.get("teamRelativeColors"), True) if active else True,
        me=me,
        allies=allies,
        enemies=enemies,
        events=events,
        fetched_at=t_now if math.isfinite(t_now) else time.monotonic(),
        current_gold=current_gold,
        champion_stats=_champion_stats(active) if me is not None else {},
    )


def parse_allgamedata(data: dict, now: float | None = None) -> GameInfo | None:
    """Parse an ``allgamedata`` payload. ``None`` for errors / loading screen. Never raises."""
    try:
        return _parse(data, now)
    except Exception:
        log.exception("Cannot parse Live Client payload")
        return None


# ---------------------------------------------------------------------------- HTTP client
class LiveClient:
    """Polls the Live Client Data API. One instance per polling thread is recommended."""

    def __init__(self, url: str = LIVE_URL, timeout: float = 1.0):
        self.url = str(url or LIVE_URL)
        t = _float(timeout, 1.0)
        self.timeout = min(30.0, max(0.05, t))
        #: Last error message ("" once the API answered), for the UI / logs.
        self.last_error: str | None = None
        #: ``time.monotonic()`` of the last successful response.
        self.last_ok: float | None = None
        self._ssl_context: ssl.SSLContext | None = None
        self._opener: urllib.request.OpenerDirector | None = None
        self._scheme = ""
        try:
            parts = urllib.parse.urlsplit(self.url)
            self._scheme = (parts.scheme or "").lower()
            host = (parts.hostname or "").lower()
            handlers: list[urllib.request.BaseHandler] = [urllib.request.ProxyHandler({})]
            if self._scheme == "https":
                if host in LOOPBACK_HOSTS:
                    # The game's certificate is self-signed (Riot root CA): skip verification,
                    # but ONLY for the local machine.
                    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                else:
                    ctx = ssl.create_default_context()
                self._ssl_context = ctx
                handlers.append(urllib.request.HTTPSHandler(context=ctx))
            if self._scheme in ("http", "https"):
                self._opener = urllib.request.build_opener(*handlers)
            else:
                log.error("Unsupported Live Client URL: %s", self.url)
        except Exception:
            log.exception("Cannot set up the Live Client HTTP client")
            self._opener = None

    def _note_error(self, msg: str) -> None:
        if msg != self.last_error:
            log.debug("Live Client API not available: %s", msg)
        self.last_error = msg

    def fetch_raw(self) -> dict | None:
        """GET the URL and decode the JSON object; ``None`` on any error. Never raises."""
        if self._opener is None:
            self._note_error("client not configured")
            return None
        try:
            req = urllib.request.Request(self.url, headers={"Accept": "application/json",
                                                            "User-Agent": "TreeAICoach"})
            deadline = time.monotonic() + max(2.0, 3.0 * self.timeout)
            chunks: list[bytes] = []
            total = 0
            with self._opener.open(req, timeout=self.timeout) as resp:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        self._note_error("response too large")
                        return None
                    if time.monotonic() > deadline:
                        self._note_error("response too slow")
                        return None
            data = json.loads(b"".join(chunks).decode("utf-8-sig", errors="replace"))
        except urllib.error.HTTPError as exc:
            try:
                exc.close()
            except Exception:
                pass
            self._note_error(f"HTTP {exc.code}")
            return None
        except (urllib.error.URLError, OSError, http.client.HTTPException, ssl.SSLError,
                ValueError) as exc:   # ValueError covers JSONDecodeError / UnicodeError
            self._note_error(f"{type(exc).__name__}: {exc}")
            return None
        except Exception as exc:
            if self.last_error != f"unexpected: {type(exc).__name__}":
                log.warning("Unexpected Live Client error", exc_info=True)
            self.last_error = f"unexpected: {type(exc).__name__}"
            return None
        if not isinstance(data, dict):
            self._note_error("unexpected JSON")
            return None
        if self.last_error:
            log.info("Live Client API reachable")
        self.last_error = ""
        self.last_ok = time.monotonic()
        return data

    def fetch(self) -> GameInfo | None:
        """Current match info, or ``None`` (no game / loading screen / error). Never raises."""
        try:
            data = self.fetch_raw()
            if data is None:
                return None
            return parse_allgamedata(data, now=time.monotonic())
        except Exception:
            log.exception("LiveClient.fetch failed")
            return None
