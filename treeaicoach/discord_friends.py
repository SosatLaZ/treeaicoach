"""Qui est qui dans le vocal : the Discord voice members matched to the allied players of the game.

The bot Discord (discord_bot.py) announces the ganks of EVERY friend in the voice channel who plays in
the game (premades on my team), not only mine. For that it must know which voice member plays which
champion: this module matches the members' Discord names (server display name, global name, user
name) against the allies' Riot ID game names (Live Client roster), with a normalisation (case,
accents, leetspeak, symbols, "#TAG") and a fuzzy score, one member to one player at most (best
scores first). The player can fix a pair by hand on the Discord page: ``Config.discord_links``
``{Discord user id: Riot ID}`` (``"-"`` = "pas dans la partie"), which always wins.

Pure functions, no Discord / Qt import, never raise. Riot IDs and Discord names stay on this PC
(config + memory), never in a log line or a diagnostic.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Iterable
from typing import Any

#: auto match accepted from this score (0..1): "Paul" ~ "PaulDu59" (contained), "Dexter" ~ "Dexterr"
MATCH_MIN = 0.72
#: a contained name must be this long to count (``"a"`` is in every name)
CONTAIN_MIN_LEN = 3
#: the manual link value meaning "this member does not play in this game"
NOT_PLAYING = "-"
LINKS_MAX = 64
NAME_SPOKEN_MAX = 18

_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s", "@": "a",
                       "!": "i", "|": "l", "€": "e"})
ROLE_FR = {"TOP": "top", "JUNGLE": "jungle", "MIDDLE": "mid", "BOTTOM": "ADC", "UTILITY": "support"}


def norm_name(s: Any) -> str:
    """A name reduced to what a human compares: no "#TAG", no accents, case-folded, leetspeak read
    as letters, only letters and digits (``"Pâul_59#EUW"`` -> ``"paulsg"``... digits kept when they
    are not leetspeak-shaped). ``""`` for anything unusable."""
    try:
        s = str(s or "").split("#", 1)[0]
        s = unicodedata.normalize("NFKD", s)
        s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()
        letters = "".join(ch for ch in s if ch.isalnum())
        if not letters:
            return ""
        bare = re.sub(r"xx+$", "", letters[2:].lstrip("x")) if letters.startswith("xx") else letters
        if len(bare) >= CONTAIN_MIN_LEN:                 # "xX_Paul_Xx" decorations
            letters = bare
        # leetspeak only inside a word that has letters ("P4ul" -> "paul"), trailing numbers stay
        # numbers ("paul59" stays "paul59": "59" is a suffix, not letters)
        m = re.match(r"^(.*?[a-z].*?)(\d*)$", letters)
        if m:
            return m.group(1).translate(_LEET) + m.group(2)
        return letters
    except Exception:
        return ""


def _core(s: str) -> str:
    """The letters part of a normalised name (trailing digits removed): "paul59" -> "paul"."""
    return re.sub(r"\d+$", "", s) or s


def name_score(a: Any, b: Any) -> float:
    """Similarity of two names in 0..1 after :func:`norm_name`: 1 identical, ~0.9 one contained in
    the other (>= 3 letters, "Paul" / "PaulDu59"), else difflib's ratio. Never raises."""
    x, y = norm_name(a), norm_name(b)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    cx, cy = _core(x), _core(y)
    if cx == cy and len(cx) >= CONTAIN_MIN_LEN:
        return 0.97
    short, long_ = (cx, cy) if len(cx) <= len(cy) else (cy, cx)
    if len(short) >= CONTAIN_MIN_LEN and short in long_:
        # "paul" in "pauldu" (0.9) ... "bob" in "bobbyjoeking" (lower): the share of the long name
        return max(0.75, 0.92 - 0.25 * (1.0 - len(short) / len(long_)))
    return difflib.SequenceMatcher(None, x, y).ratio()


def game_name(riot_id: Any) -> str:
    """``"Paul#EUW"`` -> ``"Paul"``."""
    return str(riot_id or "").split("#", 1)[0].strip()


def same_riot_id(a: Any, b: Any) -> bool:
    """Two Riot IDs are the same player (case / accents ignored; a missing tag matches any tag)."""
    sa, sb = str(a or "").strip(), str(b or "").strip()
    if not sa or not sb:
        return False
    na, nb = norm_name(sa), norm_name(sb)
    if not na or na != nb:
        return False
    ta = sa.split("#", 1)[1].strip().casefold() if "#" in sa else ""
    tb = sb.split("#", 1)[1].strip().casefold() if "#" in sb else ""
    return not ta or not tb or ta == tb


def clean_links(value: Any) -> dict[str, str]:
    """``Config.discord_links`` made safe: ``{user id (digits): Riot ID or "-"}``, bounded."""
    out: dict[str, str] = {}
    if not isinstance(value, dict):
        return out
    for k, v in list(value.items())[:LINKS_MAX * 2]:
        uid = re.sub(r"\D", "", str(k or ""))
        rid = " ".join(str(v or "").split())[:64]
        rid = "".join(ch for ch in rid if ch.isprintable())
        if 15 <= len(uid) <= 21 and rid:
            out[uid] = rid
        if len(out) >= LINKS_MAX:
            break
    return out


def role_fr(position: Any) -> str:
    return ROLE_FR.get(str(position or "").upper(), "")


def player_label(p: dict) -> str:
    """"Lux (support)" / "Lux"."""
    champ = str(p.get("champion") or p.get("alias") or "?")
    role = role_fr(p.get("position"))
    return f"{champ} ({role})" if role else champ


def spoken_name(name: Any) -> str:
    """A Discord name the voice can say: letters, digits, spaces and simple punctuation only, the
    trailing numbers / tags dropped ("xX_Paul_Xx 🎮" -> "Paul"... "Paul59" -> "Paul"), bounded.
    ``""`` when nothing pronounceable is left (the call is said without a name)."""
    try:
        s = unicodedata.normalize("NFKC", str(name or ""))
        s = "".join(ch if (ch.isalnum() or ch in " '-") else " " for ch in s)
        words = [w for w in s.split() if any(c.isalpha() for c in w)]
        # "xX" / "Xx" decorations
        words = [w for w in words if w.casefold() not in ("xx", "x")] or words
        s = " ".join(words)
        s = re.sub(r"(?<=[A-Za-zÀ-ÿ])\d+$", "", s).strip(" '-")
        s = re.sub(r"^(?:x+)(?=[A-Z])", "", s)       # "xPaul" -> "Paul"
        s = s[:NAME_SPOKEN_MAX].strip(" '-")
        return s if sum(c.isalpha() for c in s) >= 2 else ""
    except Exception:
        return ""


def best_spoken(names: Iterable[Any]) -> str:
    """The name to say among a member's names: the first one the voice reads well (no digit left:
    "xX_P4ul_Xx" is skipped for "Paul"), else the first sayable one, else ``""``."""
    said = [spoken_name(n) for n in names or ()]
    return next((n for n in said if n and not any(c.isdigit() for c in n)), next((n for n in said if n), ""))


def member_names(m: dict) -> list[str]:
    """Every name of a voice member worth comparing (display, global, user name), distinct."""
    out: list[str] = []
    for k in ("display", "global", "name"):
        v = str(m.get(k) or "").strip()
        if v and v not in out:
            out.append(v)
    return out


def match(members: Iterable[dict], players: Iterable[dict], links: dict[str, str] | None = None,
          me_id: Any = "") -> list[dict]:
    """Who plays what. ``members``: the voice channel's people ``{id, display, global, name}``
    (the bot excluded); ``players``: my team in the game ``{riot, champion, alias, position, me}``;
    ``links``: manual pairs ``{user id: Riot ID or "-"}``; ``me_id``: my own Discord id (I am the
    ``me`` player whatever my names say).

    Returns one entry per member, in the members' order: ``{id, name, player (dict or None),
    source ("toi" / "manuel" / "auto" / "absent" / "inconnu"), score}``. One player has one member at
    most: manual links first, then me, then the best automatic scores. Never raises."""
    try:
        mem = [dict(m) for m in members or [] if isinstance(m, dict) and str(m.get("id") or "")]
        pls = [dict(p) for p in players or [] if isinstance(p, dict)]
        links = clean_links(links or {})
        me_id = str(me_id or "")
        res: dict[str, dict] = {}
        taken: set[int] = set()

        def entry(m: dict, idx: int | None, source: str, score: float = 0.0) -> dict:
            names = member_names(m)
            return {"id": str(m["id"]), "name": names[0] if names else "?", "spoken": best_spoken(names),
                    "player": pls[idx] if idx is not None else None, "source": source,
                    "score": round(float(score), 3)}

        for m in mem:                               # 1. the player's own choices
            want = links.get(str(m["id"]))
            if want is None:
                continue
            if want == NOT_PLAYING:
                res[str(m["id"])] = entry(m, None, "absent")
                continue
            idx = next((i for i, p in enumerate(pls) if i not in taken and same_riot_id(p.get("riot"), want)), None)
            if idx is not None:
                taken.add(idx)
                res[str(m["id"])] = entry(m, idx, "manuel", 1.0)
            # a link to someone not in this game: matched automatically below (another game)
        if me_id:                                   # 2. me
            m = next((x for x in mem if str(x["id"]) == me_id and me_id not in res), None)
            idx = next((i for i, p in enumerate(pls) if p.get("me") and i not in taken), None)
            if m is not None and idx is not None:
                taken.add(idx)
                res[me_id] = entry(m, idx, "toi", 1.0)
        cands: list[tuple[float, int, int]] = []    # 3. best automatic scores, one-to-one
        for mi, m in enumerate(mem):
            if str(m["id"]) in res:
                continue
            for pi, p in enumerate(pls):
                if pi in taken or (p.get("me") and me_id):
                    continue
                gn = game_name(p.get("riot"))
                sc = max((name_score(n, gn) for n in member_names(m)), default=0.0)
                if sc >= MATCH_MIN:
                    cands.append((sc, mi, pi))
        cands.sort(key=lambda c: (-c[0], c[1], c[2]))
        used_m: set[int] = set()
        for sc, mi, pi in cands:
            if mi in used_m or pi in taken:
                continue
            used_m.add(mi)
            taken.add(pi)
            res[str(mem[mi]["id"])] = entry(mem[mi], pi, "auto", sc)
        out = []
        for m in mem:
            e = res.get(str(m["id"]))
            out.append(e if e is not None else entry(m, None, "inconnu"))
        return out
    except Exception:
        return []


def describe(entry: dict, in_game: bool) -> str:
    """The page's line for one member: "Lux (support)" / "pas dans la partie" / "en attente de la partie"."""
    p = entry.get("player")
    if p:
        return player_label(p)
    if entry.get("source") == "absent":
        return "pas dans la partie (choisi à la main)"
    return "pas dans la partie" if in_game else "en attente de la partie"


__all__ = ["norm_name", "name_score", "match", "same_riot_id", "clean_links", "spoken_name", "best_spoken",
           "player_label",
           "role_fr", "game_name", "describe", "NOT_PLAYING", "MATCH_MIN"]
