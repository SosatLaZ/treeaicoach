"""Public ranked info of the players of the game in progress (OP.GG / Porofessor style), read-only.

The user asked for what OP.GG / Porofessor show for his current game: rank, LP, season wins /
losses, current win / loss streak, games on the champion played. Mandatory scope (privacy):

* only PUBLIC ranked data the League Client already exposes locally (:mod:`treeaicoach.lcu`, GET
  only, loopback only): no Riot API key, no third-party site;
* only once the game is IN PROGRESS (gameflow phase ``InProgress``, after loading). Nothing is read
  during champion select: hidden names of ranked champion select are never de-anonymised;
* kept in memory for the current game only (cleared when the game ends / the coach stops); the
  player ids (PUUIDs) are used for the requests only and never logged, written to a record, a report
  or a test. Names / Riot IDs are never read. Everything is shown BY CHAMPION
  ("Leur jungler (Lee Sin) : Platine 2, 3 défaites d'affilée");
* off by default (``Config.player_intel``, switch Réglages > "Rangs des joueurs").

Endpoints (all GET):

* ``/lol-gameflow/v1/session`` - phase, game id, queue, teamOne / teamTwo (puuid, championId);
* ``/lol-summoner/v1/current-summoner`` - my puuid (to know which team is mine; I am skipped);
* ``/lol-ranked/v1/ranked-stats/{puuid}`` - tier, division, LP, wins / losses of the queue;
* ``/lol-match-history/v1/products/lol/{puuid}/matches?begIndex=0&endIndex=9`` - the last 10 games
  -> current streak, games (and wins) on the champion played now.

:class:`PlayerIntelWatcher` fetches in its own daemon thread, at most :data:`MAX_RPS` requests per
second, each answer cached for the game; the engine / UI only read :meth:`snapshot` (a lock, no
I/O): nothing ever blocks. The thread stops by itself when nobody asked for it for
:data:`IDLE_STOP_S` and then forgets everything. :func:`decisive_lines` picks at most
:data:`MAX_COACH_LINES` coach lines ("Leur jungler (Lee Sin) est en série de 4 défaites"), sent
through the engine's presenter path by :func:`engine_tick`. Never raises.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable

log = logging.getLogger(__name__)

SESSION_PATH = "/lol-gameflow/v1/session"
ME_PATH = "/lol-summoner/v1/current-summoner"
RANKED_PATH = "/lol-ranked/v1/ranked-stats/{puuid}"
HISTORY_PATH = "/lol-match-history/v1/products/lol/{puuid}/matches?begIndex=0&endIndex=9"

MAX_RPS = 3.0               # at most 3 requests per second to the client
SESSION_RECHECK_S = 15.0    # the gameflow phase is re-read this often once the game is known
IDLE_STOP_S = 30.0          # the thread stops (and forgets) when nobody polled it for this long
REMAKE_S = 300.0            # games shorter than this are remakes: not a win nor a loss
MAX_COACH_LINES = 2
LOSS_STREAK_MIN = 4         # "série de 4 défaites" and more is worth a coach line
WIN_STREAK_MIN = 5
NEW_CHAMP_MIN_GAMES = 8     # "pas joué sur ses 10 dernières" only when the history is that long
COACH_FROM_S = 20.0         # coach lines from this game time ...
COACH_UNTIL_S = 240.0       # ... until this one (early game info, useless later)
COACH_RETRY_S = 10.0
#: Discord rank announcement (``discord_ranks``): sent once per game, as soon as the data is read, until this
#: game time; at most this many spoken lines (lane highlights), a streak from this length
RANKS_UNTIL_S = 180.0
RANKS_MAX_VOICE = 3
RANKS_STREAK_MIN = 3

TIER_FR = {"IRON": "Fer", "BRONZE": "Bronze", "SILVER": "Argent", "GOLD": "Or", "PLATINUM": "Platine",
           "EMERALD": "Émeraude", "DIAMOND": "Diamant", "MASTER": "Maître", "GRANDMASTER": "Grand maître",
           "CHALLENGER": "Challenger"}
APEX = ("MASTER", "GRANDMASTER", "CHALLENGER")
DIVISION = {"I": 1, "II": 2, "III": 3, "IV": 4}
ROLE_FR = {"TOP": "top", "JUNGLE": "jungler", "MIDDLE": "mid", "BOTTOM": "tireur", "UTILITY": "support"}
ROLE_ORDER = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY", "")
_POS = {"top": "TOP", "jungle": "JUNGLE", "middle": "MIDDLE", "mid": "MIDDLE", "bottom": "BOTTOM",
        "bot": "BOTTOM", "adc": "BOTTOM", "utility": "UTILITY", "support": "UTILITY"}
FLEX_QUEUES = (440,)


def _int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except (TypeError, ValueError, OverflowError):
        return default


@dataclass(frozen=True)
class PlayerIntel:
    """One player of the current game, by champion (no name, no id)."""
    alias: str                  # "LeeSin"
    name: str                   # "Lee Sin"
    ally: bool
    role: str = ""              # "TOP" ... "UTILITY" or ""
    ranked_loaded: bool = False
    tier: str = ""              # "PLATINUM" ("" = unranked)
    division: int = 0           # 1..4 (0 for apex tiers)
    lp: int = 0
    wins: int = 0
    losses: int = 0
    history_loaded: bool = False
    games: int = 0              # recent games read (<= 10, remakes excluded)
    streak: int = 0             # +n wins in a row, -n losses in a row (most recent first)
    champ_games: int = 0        # recent games on the champion played now
    champ_wins: int = 0


# ----------------------------------------------------------------------------- parsing (pure)
def parse_session(session: Any) -> dict | None:
    """Gameflow session -> ``{game_id, queue_id, players: [(puuid, champion_id, team 1|2, position)]}``.
    None unless the phase is ``InProgress`` (never during champion select). Never raises."""
    try:
        if not isinstance(session, dict) or str(session.get("phase") or "") != "InProgress":
            return None
        gd = session.get("gameData") if isinstance(session.get("gameData"), dict) else {}
        if gd.get("isCustomGame") is True:
            return None
        queue = gd.get("queue") if isinstance(gd.get("queue"), dict) else {}
        picks = {}
        for sel in gd.get("playerChampionSelections") or ():
            if isinstance(sel, dict) and sel.get("puuid"):
                picks[str(sel["puuid"])] = _int(sel.get("championId"))
        players = []
        for team_no, key in ((1, "teamOne"), (2, "teamTwo")):
            for p in gd.get(key) or ():
                if not isinstance(p, dict):
                    continue
                puuid = str(p.get("puuid") or "")
                if not puuid:
                    continue
                cid = _int(p.get("championId")) or picks.get(puuid, 0)
                pos = _POS.get(str(p.get("selectedPosition") or "").strip().lower(), "")
                players.append((puuid, cid, team_no, pos))
        if not players:
            return None
        return {"game_id": _int(gd.get("gameId")), "queue_id": _int(queue.get("id")), "players": players}
    except Exception:
        log.debug("gameflow session not parsed", exc_info=True)
        return None


def parse_ranked(data: Any, queue_id: int = 420) -> dict | None:
    """Ranked stats -> ``{tier, division, lp, wins, losses}`` of the solo queue (flex for a flex
    game). Unranked -> tier "". None when the answer is unusable. Never raises."""
    try:
        if not isinstance(data, dict):
            return None
        want = "RANKED_FLEX_SR" if queue_id in FLEX_QUEUES else "RANKED_SOLO_5x5"
        entry = None
        qm = data.get("queueMap")
        if isinstance(qm, dict) and isinstance(qm.get(want), dict):
            entry = qm[want]
        if entry is None:
            for q in data.get("queues") or ():
                if isinstance(q, dict) and q.get("queueType") == want:
                    entry = q
                    break
        if entry is None:
            return {"tier": "", "division": 0, "lp": 0, "wins": 0, "losses": 0}
        tier = str(entry.get("tier") or "").upper()
        if tier not in TIER_FR:
            tier = ""
        return {"tier": tier, "division": 0 if tier in APEX or not tier else DIVISION.get(str(entry.get("division")), 0),
                "lp": max(0, _int(entry.get("leaguePoints"))), "wins": max(0, _int(entry.get("wins"))),
                "losses": max(0, _int(entry.get("losses")))}
    except Exception:
        log.debug("ranked stats not parsed", exc_info=True)
        return None


def _games_list(data: Any) -> list | None:
    if not isinstance(data, dict):
        return None
    g = data.get("games")
    g = g.get("games") if isinstance(g, dict) else g
    return g if isinstance(g, list) else None


def _own_participant(game: dict, puuid: str) -> dict | None:
    parts = [p for p in game.get("participants") or () if isinstance(p, dict)]
    if len(parts) == 1:                       # another player's history: only that player is listed
        return parts[0]
    pid = None
    for ident in game.get("participantIdentities") or ():
        if isinstance(ident, dict) and isinstance(ident.get("player"), dict) and ident["player"].get("puuid") == puuid:
            pid = ident.get("participantId")
            break
    for p in parts:
        if pid is not None and p.get("participantId") == pid:
            return p
    return None


def parse_history(data: Any, puuid: str, champion_id: int) -> dict | None:
    """Match history -> ``{games, streak, champ_games, champ_wins}`` (most recent game first;
    remakes skipped). None when unusable. Never raises."""
    try:
        games = _games_list(data)
        if games is None:
            return None
        rows = []
        for g in games:
            if not isinstance(g, dict):
                continue
            if 0 < _int(g.get("gameDuration")) < REMAKE_S:
                continue
            p = _own_participant(g, puuid)
            st = p.get("stats") if isinstance(p, dict) and isinstance(p.get("stats"), dict) else None
            if st is None or not isinstance(st.get("win"), bool) or st.get("gameEndedInEarlySurrender") is True:
                continue
            rows.append((_int(g.get("gameCreation")), bool(st["win"]), _int(p.get("championId"))))
        rows.sort(key=lambda r: r[0], reverse=True)
        streak = 0
        for _c, win, _ch in rows:
            if streak == 0:
                streak = 1 if win else -1
            elif (streak > 0) == win:
                streak += 1 if win else -1
            else:
                break
        on = [r for r in rows if champion_id and r[2] == champion_id]
        return {"games": len(rows), "streak": streak, "champ_games": len(on), "champ_wins": sum(1 for r in on if r[1])}
    except Exception:
        log.debug("match history not parsed", exc_info=True)
        return None


# ----------------------------------------------------------------------------- wording (pure)
def rank_text(p: PlayerIntel) -> str:
    """"Platine 2 · 45 LP" / "Maître · 120 LP" / "Non classé" ("" while unknown)."""
    if not p.ranked_loaded:
        return ""
    if not p.tier:
        return "Non classé"
    name = TIER_FR[p.tier] + (f" {p.division}" if p.division else "")
    return f"{name} · {p.lp} LP"


def streak_text(streak: int) -> str:
    if streak >= 2:
        return f"{streak} victoires d'affilée"
    if streak <= -2:
        return f"{-streak} défaites d'affilée"
    return ""


def who(p: PlayerIntel) -> str:
    """"Leur jungler (Lee Sin)" / "Ton support (Lulu)" / "Leur Lee Sin" without a role."""
    head = "Leur" if not p.ally else "Ton"
    role = ROLE_FR.get(p.role, "")
    return f"{head} {role} ({p.name})" if role else f"{head} {p.name}"


def card_line(p: PlayerIntel) -> str:
    """One compact line of the Accueil card, by champion."""
    role = ROLE_FR.get(p.role, "")
    parts = [f"{p.name}" + (f" ({role})" if role else "")]
    r = rank_text(p)
    if r:
        parts.append(r)
    if p.ranked_loaded and p.wins + p.losses > 0:
        n = p.wins + p.losses
        parts.append(f"{p.wins} V {p.losses} D ({round(100 * p.wins / n)} %)")
    if p.history_loaded:
        s = streak_text(p.streak)
        if s:
            parts.append(s)
        if p.games:
            parts.append(f"{p.champ_games} sur {p.name} ({p.games} dernières)")
    if len(parts) == 1:
        parts.append("chargement…")
    return " · ".join(parts)


def card(players: list[PlayerIntel] | tuple[PlayerIntel, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(enemy lines, ally lines), each sorted top -> support."""
    def key(p: PlayerIntel) -> int:
        return ROLE_ORDER.index(p.role) if p.role in ROLE_ORDER else len(ROLE_ORDER)
    enemies = tuple(card_line(p) for p in sorted((p for p in players if not p.ally), key=key))
    allies = tuple(card_line(p) for p in sorted((p for p in players if p.ally), key=key))
    return enemies, allies


def decisive_lines(players: list[PlayerIntel] | tuple[PlayerIntel, ...], my_role: str = "") -> list[tuple[str, str]]:
    """At most :data:`MAX_COACH_LINES` ``(topic, text)`` coach lines about ENEMIES, only when
    decisive: a long loss streak, a long win streak, a champion not played in the last games.
    The lane opponent and the jungler come first."""
    cands: list[tuple[float, str, str]] = []
    for p in players:
        if p.ally or not p.history_loaded:
            continue
        prio = 2.0 if my_role and p.role == my_role else 1.0 if p.role == "JUNGLE" else 0.0
        if p.streak <= -LOSS_STREAK_MIN:
            cands.append((prio + 0.3, f"loss:{p.alias}", f"{who(p)} est en série de {-p.streak} défaites"))
        elif p.streak >= WIN_STREAK_MIN:
            cands.append((prio + 0.2, f"win:{p.alias}", f"{who(p)} est en série de {p.streak} victoires : joue prudent"))
        if p.games >= NEW_CHAMP_MIN_GAMES and p.champ_games == 0:
            role = ROLE_FR.get(p.role, "")
            text = (f"Leur {role} n'a pas joué {p.name} sur ses {p.games} dernières parties" if role
                    else f"{p.name} ennemi : pas joué sur ses {p.games} dernières parties")
            cands.append((prio + 0.1, f"new:{p.alias}", text))
    cands.sort(key=lambda c: -c[0])
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for _s, topic, text in cands:
        alias = topic.split(":", 1)[1]
        if alias in seen:
            continue
        seen.add(alias)
        out.append((topic, text))
        if len(out) >= MAX_COACH_LINES:
            break
    return out


# ----------------------------------------------------------------------------- watcher
def _default_resolver() -> Callable[[int], tuple[str, str] | None]:
    def resolve(cid: int) -> tuple[str, str] | None:
        try:
            from treeaicoach.champions import get_default_db

            e = get_default_db().by_key(cid)
            return (e.alias, e.name_fr or e.alias) if e is not None else None
        except Exception:
            return None
    return resolve


class PlayerIntelWatcher:
    """Fetches the current game's player info in a daemon thread (<= :data:`MAX_RPS` req/s);
    :meth:`snapshot` is lock-only. Thread-safe, never raises."""

    def __init__(self, client: Any = None, resolve: Callable[[int], tuple[str, str] | None] | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] | None = None) -> None:
        self._client = client
        self._resolve = resolve or _default_resolver()
        self._clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sleep = sleep or (lambda s: self._stop.wait(s))
        self._thread: threading.Thread | None = None
        self._last_wanted = -1e18
        self._last_req = -1e18
        self.requests = 0
        self._reset_game()

    # -------------------------------------------------------------- state
    def _reset_game(self) -> None:
        with self._lock:
            self._game_id: int | None = None
            self._queue_id = 0
            self._my_puuid = ""
            self._todo: list[tuple[str, str]] = []           # (kind, puuid) still to fetch
            self._players: dict[str, PlayerIntel] = {}       # puuid -> info (memory only)
            self._champ: dict[str, int] = {}
            self._roles: dict[str, str] = {}                 # alias -> position (live client)
            self._next_session = -1e18

    def forget(self) -> None:
        """Drop everything about the current game (memory only, nothing was ever written)."""
        self._reset_game()

    def snapshot(self) -> tuple[PlayerIntel, ...]:
        with self._lock:
            roles = dict(self._roles)
            out = []
            for p in self._players.values():
                r = roles.get(p.alias)
                out.append(replace(p, role=r) if r is not None and r != p.role else p)
            return tuple(out)

    def ready(self) -> bool:
        """True once every request of the game was answered (or given up)."""
        with self._lock:
            return bool(self._players) and not self._todo

    def set_roles(self, roles: dict[str, str]) -> None:
        """Champion alias -> position from the Live Client (beats the session's selectedPosition)."""
        with self._lock:
            self._roles = {str(k): str(v) for k, v in (roles or {}).items() if v in ROLE_FR}

    # -------------------------------------------------------------- fetching
    def _get_client(self) -> Any:
        if self._client is None:
            from treeaicoach.lcu import get_default_client
            self._client = get_default_client()
        return self._client

    def _get(self, path: str) -> Any:
        """One request, spaced by at least 1 / MAX_RPS seconds."""
        wait = self._last_req + 1.0 / MAX_RPS - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last_req = self._clock()
        self.requests += 1
        client = self._get_client()
        return client.get(path) if client is not None else None

    def step(self) -> bool:
        """One unit of work (at most one request). False when there is nothing to do now."""
        try:
            now = self._clock()
            if now >= self._next_session:
                self._next_session = now + SESSION_RECHECK_S
                self._check_session()
                return True
            with self._lock:
                job = self._todo[0] if self._todo else None
            if job is None:
                return False
            kind, puuid = job
            with self._lock:
                qid, cid = self._queue_id, self._champ.get(puuid, 0)
            if kind == "ranked":
                parsed = parse_ranked(self._get(RANKED_PATH.format(puuid=puuid)), qid)
            else:
                parsed = parse_history(self._get(HISTORY_PATH.format(puuid=puuid)), puuid, cid)
            with self._lock:
                if self._todo and self._todo[0] == job:
                    self._todo.pop(0)
                p = self._players.get(puuid)
                if p is not None and parsed is not None:
                    if kind == "ranked":
                        self._players[puuid] = replace(p, ranked_loaded=True, **parsed)
                    else:
                        self._players[puuid] = replace(p, history_loaded=True, **parsed)
            return True
        except Exception:
            log.debug("player intel step failed", exc_info=True)
            return False

    def _check_session(self) -> None:
        info = parse_session(self._get(SESSION_PATH))
        if info is None:
            if self._game_id is not None:
                self._reset_game()                       # the game ended: forget it
            return
        if info["game_id"] == self._game_id and self._players:
            return
        self._reset_game()
        me = self._get(ME_PATH)
        my = str(me.get("puuid") or "") if isinstance(me, dict) else ""
        my_team = next((t for pu, _c, t, _p in info["players"] if pu == my), 0)
        players: dict[str, PlayerIntel] = {}
        champ: dict[str, int] = {}
        for puuid, cid, team, pos in info["players"]:
            if puuid == my or cid <= 0:
                continue
            r = self._resolve(cid)
            if r is None:
                continue
            players[puuid] = PlayerIntel(alias=r[0], name=r[1] or r[0], ally=bool(my_team) and team == my_team,
                                         role=pos)
            champ[puuid] = cid
        with self._lock:
            self._game_id = info["game_id"]
            self._queue_id = info["queue_id"]
            self._my_puuid = my
            self._players = players
            self._champ = champ
            # enemies first (what the player wants), ranked then history
            order = sorted(players, key=lambda k: players[k].ally)
            self._todo = [("ranked", k) for k in order] + [("history", k) for k in order]
            self._next_session = self._clock() + SESSION_RECHECK_S
        log.info("Player info: game in progress, %d players to read", len(players))

    # -------------------------------------------------------------- thread
    def want(self) -> None:
        """Ask for the info (starts the thread); it stops when not asked for :data:`IDLE_STOP_S`."""
        self._last_wanted = self._clock()
        t = self._thread
        if t is not None and t.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="TreeAI-player-intel", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                if self._clock() - self._last_wanted > IDLE_STOP_S:
                    break
                if not self.step():
                    self._stop.wait(1.0)
        finally:
            self.forget()


_default: PlayerIntelWatcher | None = None
_default_lock = threading.Lock()


def get_default_watcher() -> PlayerIntelWatcher:
    global _default
    with _default_lock:
        if _default is None:
            _default = PlayerIntelWatcher()
        return _default


def enemy_intel(alias: Any) -> PlayerIntel | None:
    """The enemy ``alias``'s info from the running watcher (never starts one, never fetches): what
    the in-game advice reads (draft_edge.lane_prior). None when off / not read yet. Never raises."""
    try:
        w = _default
        a = str(alias or "").lower()
        if w is None or not a:
            return None
        return next((p for p in w.snapshot() if not p.ally and p.alias.lower() == a), None)
    except Exception:
        return None


def enabled(cfg: Any) -> bool:
    return bool(getattr(cfg, "player_intel", False)) and bool(getattr(cfg, "lcu_enabled", True))


def _short_rank(p: PlayerIntel) -> str:
    """"Platine 2" / "Maître 120 LP" / "non classé" / "" (unknown)."""
    if not p.ranked_loaded:
        return ""
    if not p.tier:
        return "non classé"
    if p.tier in APEX:
        return f"{TIER_FR[p.tier]} {p.lp} LP"
    return TIER_FR[p.tier] + (f" {p.division}" if p.division else "")


def _highlight(p: PlayerIntel) -> str:
    """The one notable fact of a player for the announcement ("" = nothing notable)."""
    if not p.history_loaded:
        return ""
    if p.streak <= -RANKS_STREAK_MIN:
        return f"{-p.streak} défaites d'affilée"
    if p.streak >= RANKS_STREAK_MIN:
        return f"{p.streak} victoires d'affilée"
    if p.games >= NEW_CHAMP_MIN_GAMES and p.champ_games == 0:
        return f"pas joué {p.name} récemment"
    return ""


def discord_ranks(players: list[PlayerIntel] | tuple[PlayerIntel, ...],
                  my_role: str = "") -> tuple[str, list[str]]:
    """``(text message, voice lines)`` of the game-start rank announcement on Discord, BY CHAMPION (never
    a name or an id). The message: one line per team, sorted top -> support. The voice: at most
    :data:`RANKS_MAX_VOICE` short lines, only the enemies with a notable fact (streak, new champion),
    my lane opponent and the jungler first: "Leur jungler Lee Sin : Platine 2, 4 défaites d'affilée".
    ``("", [])`` while nothing is known. Pure, never raises."""
    try:
        def order(p: PlayerIntel) -> int:
            return ROLE_ORDER.index(p.role) if p.role in ROLE_ORDER else len(ROLE_ORDER)

        def entry(p: PlayerIntel) -> str:
            bits = [b for b in (_short_rank(p), _highlight(p)) if b]
            return f"{p.name} " + (", ".join(bits) if bits else "?")

        if not any(p.ranked_loaded or p.history_loaded for p in players):
            return "", []
        lines = ["Rangs de la partie"]
        for title, ally in (("Ennemis", False), ("Alliés", True)):
            team = sorted((p for p in players if p.ally is ally), key=order)
            if team:
                lines.append(f"{title} : " + " · ".join(entry(p) for p in team))
        voice: list[tuple[float, str]] = []
        for p in players:
            hl = _highlight(p)
            if p.ally or not hl:
                continue
            prio = 2.0 if my_role and p.role == my_role else 1.0 if p.role == "JUNGLE" else 0.0
            role = ROLE_FR.get(p.role, "")
            head = f"Leur {role} {p.name}" if role else f"{p.name} ennemi"
            said = ", ".join(b for b in (_short_rank(p), hl) if b)
            voice.append((prio + min(abs(p.streak), 9) / 10.0, f"{head} : {said}"))
        voice.sort(key=lambda v: -v[0])
        return "\n".join(lines), [v[1] for v in voice[:RANKS_MAX_VOICE]]
    except Exception:
        log.debug("discord ranks failed", exc_info=True)
        return "", []


def ranks_tick(engine: Any, gt: float, w: Any, my_role: str, state: dict) -> None:
    """Once per game, as soon as every player is read: the rank announcement to the Discord bot (kind
    ``ranks``) when ``discord_ranks`` is on; given up after :data:`RANKS_UNTIL_S`. Never raises."""
    try:
        if state.get("ranks_sent") or not getattr(getattr(engine, "_cfg", None), "discord_ranks", True):
            return
        if not w.ready():
            if gt >= RANKS_UNTIL_S:
                state["ranks_sent"] = True           # too late for a game-start announcement
            return
        state["ranks_sent"] = True
        text, voice = discord_ranks(w.snapshot(), my_role)
        call = getattr(engine, "_call", None)
        if text and callable(call):
            call("ranks", text, {"lines": voice})
    except Exception:
        log.debug("ranks tick failed", exc_info=True)


# ----------------------------------------------------------------------------- engine hook
def engine_tick(engine: Any, t: float, watcher: PlayerIntelWatcher | None = None) -> None:
    """Called by the engine about once a second during a game: keeps the watcher alive, gives it
    the Live Client positions, and offers at most :data:`MAX_COACH_LINES` decisive lines through
    the engine's presenter path (``engine._toast``). Never raises."""
    try:
        cfg = getattr(engine, "_cfg", None)
        if not enabled(cfg) or getattr(engine, "_demo", False):
            return
        game = getattr(engine, "_game", None)
        gt = getattr(game, "game_time", None)
        if game is None or gt is None:
            return
        w = watcher or get_default_watcher()
        w.want()
        roles = {}
        me = getattr(game, "me", None)
        for p in [me] + list(getattr(game, "allies", None) or ()) + list(getattr(game, "enemies", None) or ()):
            alias, pos = str(getattr(p, "champion_alias", "") or ""), str(getattr(p, "position", "") or "")
            if alias and pos:
                roles[alias] = pos
        if roles:
            w.set_roles(roles)
        state = getattr(engine, "_player_intel_state", None)
        if not isinstance(state, dict) or float(gt) < state.get("gt", 0.0) - 5.0:     # new game
            state = {"done": set(), "next": 0.0, "gt": float(gt)}
            engine._player_intel_state = state
        state["gt"] = float(gt)
        my_role = str(getattr(me, "position", "") or "")
        ranks_tick(engine, float(gt), w, my_role, state)
        if not (COACH_FROM_S <= float(gt) <= COACH_UNTIL_S) or float(gt) < state["next"]:
            return
        if len(state["done"]) >= MAX_COACH_LINES or not w.ready():
            return
        toast = getattr(engine, "_toast", None)
        lines = decisive_lines(w.snapshot(), my_role)
        if not lines:
            state["next"] = float("inf")             # nothing decisive this game: never ask again
            return
        for topic, text in lines:
            if topic in state["done"]:
                continue
            if len(state["done"]) >= MAX_COACH_LINES:
                break
            alias = topic.split(":", 1)[1]
            shown = bool(toast("info", "JOUEURS", text, alias, f"intel:{topic}", t)) if callable(toast) else False
            state["next"] = float(gt) + COACH_RETRY_S
            if shown:
                state["done"].add(topic)
            return                                   # one line per try
    except Exception:
        log.debug("player intel tick failed", exc_info=True)


__all__ = ["PlayerIntel", "PlayerIntelWatcher", "parse_session", "parse_ranked", "parse_history", "card",
           "card_line", "decisive_lines", "enemy_intel", "discord_ranks", "ranks_tick", "rank_text", "streak_text", "who",
           "engine_tick", "enabled",
           "get_default_watcher"]
