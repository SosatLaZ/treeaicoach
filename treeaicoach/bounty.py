"""Kill streaks -> bounty calls ("tu es la cible", "leur carry vaut une grosse prime").

The Live Client has no bounty field; the kill streak (kills since the last death) is rebuilt from
the public ``ChampionKill`` events (killer / victim names -> players) and the big bounties follow
it: a champion on a 3+ kill streak carries a shutdown bounty (the in-game "SHUTDOWN" gold).

Measured on the LCU truth of 31 games (2026-10, ``tools``-style replay of every kill): a player
dies within 2 min in 46 % of the minutes; right after reaching a streak of 3 / 4 / 5+ it is 41 /
38 / 41 % (shutdowns are common: 555 streaks of 3+), and the player of these games on a 5+ streak
died within 2 min in 15 / 26 cases (58 %), above his base rate: the shutdown target is the one
who keeps fighting. Two calls, written only (no voice), never during a fight (``live_edge`` busy):

* me on a streak of 3+: "Rentre dépenser tes 1 400 PO : ta tête vaut une prime (série de 4)"
  (gold >= :data:`SPEND_GOLD`) or "Ne te bats pas seul : ...";
* the enemy with the longest 3+ streak: "Vise Caitlyn en premier : énorme prime (série de 5)".

Pure, no I/O, never raises. Legal data only (public kill feed).
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

STREAK_MIN = 3            # a shutdown bounty from this streak on
BIG_STREAK = 5            # "énorme prime"
SPEND_GOLD = 1000.0       # me on a streak with this much unspent gold: go spend it


def _norm(name: Any) -> str:
    if not isinstance(name, str):
        return ""
    s = unicodedata.normalize("NFC", name).replace(" ", " ").split("#", 1)[0]
    return " ".join(s.split()).casefold()


def _lookup(players: Iterable[Any]) -> dict[str, str]:
    """Normalized riot id / summoner name / champion name -> champion alias (lower)."""
    out: dict[str, str] = {}
    for p in players:
        a = str(getattr(p, "champion_alias", "") or "").lower()
        if not a:
            continue
        for attr in ("riot_id", "summoner_name", "champion_name"):
            k = _norm(getattr(p, attr, ""))
            if k:
                out.setdefault(k, a)
    return out


def streaks(events: Iterable[Any], players: Iterable[Any]) -> dict[str, int]:
    """``alias (lower) -> kills since his last death`` from the ``ChampionKill`` events (time order)."""
    look = _lookup(players)
    out: dict[str, int] = {a: 0 for a in set(look.values())}
    kills = []
    for ev in events or ():
        if isinstance(ev, Mapping) and ev.get("EventName") == "ChampionKill":
            try:
                t = float(ev.get("EventTime"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(t):
                kills.append((t, look.get(_norm(ev.get("KillerName"))), look.get(_norm(ev.get("VictimName")))))
    for _t, killer, victim in sorted(kills, key=lambda k: k[0]):
        if victim is not None:
            out[victim] = 0
        if killer is not None and killer != victim:
            out[killer] = out.get(killer, 0) + 1
    return out


def _bucket(n: int) -> int:
    """Call identity: a streak of 3-4 then 5+ (one call each, not one per kill)."""
    return BIG_STREAK if n >= BIG_STREAK else STREAK_MIN


def _name(p: Any) -> str:
    return str(getattr(p, "champion_name", "") or getattr(p, "champion_alias", "") or "")


def _gold_fr(g: float) -> str:
    return f"{int(g // 50 * 50):,}".replace(",", " ")


def bounty_call(game: Any) -> tuple[str, str] | None:
    """``(card line, ident)`` or None: me first (I am the target), else their best-streak enemy."""
    try:
        me = getattr(game, "me", None)
        if me is None:
            return None
        allies = list(getattr(game, "allies", None) or [])
        enemies = list(getattr(game, "enemies", None) or [])
        st = streaks(getattr(game, "events", None) or [], [me] + allies + enemies)
        my_alias = str(getattr(me, "champion_alias", "") or "").lower()
        mine = st.get(my_alias, 0)
        if mine >= STREAK_MIN and not bool(getattr(me, "is_dead", False)):
            gold = float(getattr(game, "current_gold", 0.0) or 0.0)
            why = f"ta tête vaut une {'énorme ' if mine >= BIG_STREAK else ''}prime (série de {mine})"
            what = f"Rentre dépenser tes {_gold_fr(gold)} PO" if gold >= SPEND_GOLD else "Ne te bats pas seul"
            return f"{what} : {why}", f"me:{_bucket(mine)}"
        best = None
        for p in enemies:
            a = str(getattr(p, "champion_alias", "") or "").lower()
            n = st.get(a, 0)
            if n >= STREAK_MIN and not bool(getattr(p, "is_dead", False)) and (best is None or n > best[0]):
                best = (n, p, a)
        if best is not None:
            n, p, a = best
            size = "énorme" if n >= BIG_STREAK else "grosse"
            return f"Vise {_name(p)} en premier : {size} prime (série de {n})", f"{a}:{_bucket(n)}"
        return None
    except Exception:
        return None
