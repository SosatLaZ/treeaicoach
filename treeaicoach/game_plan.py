"""Game plan: the lane matchup card at game start + objective / power-play facts for the tips.

* :func:`matchup_card` - at game start, from the bundled champion profiles (:mod:`treeaicoach.meta`:
  power curve, range, play style, mobility) and the public roster of the Live Client API: two
  short lines of lane plan against my lane opponent ("Darius est plus fort tôt : farme prudemment
  jusqu'au niveau 6", "Il a moins de portée : tape-le quand il prend un sbire") and one line about
  the enemy jungler ("Balise ta rivière avant 2:30 : Lee Sin ganke tôt" - 2026: camps spawn at 0:55), with the side of his
  PROBABLE first gank (the enemy side lane with the strongest early game / most crowd control:
  a heuristic, always labelled "probable"). Junglers get a "first gank" lane instead.
* :func:`map_fields` - soul point, Baron / Elder buff owner and time left from
  :class:`treeaicoach.phase.MapState`, as :class:`treeaicoach.tips.TipContext` fields.
* :data:`OBJ_ROLES` - which roles an objective concerns (the recall-timing tips).

Nothing about enemy cooldowns or summoner spells. Pure Python, never raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

CURVE_RANK = {"early": 2, "mid": 1, "late": 0}
SIDE_FR = {"top": "en haut", "bot": "en bas"}
#: roles concerned by each objective (recall timing / "don't recall now")
OBJ_ROLES: dict[str, tuple[str, ...]] = {
    "dragon": ("JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"),
    "grubs": ("JUNGLE", "TOP", "MIDDLE"),
    "herald": ("JUNGLE", "TOP", "MIDDLE"),
    "baron": ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"),
    "elder": ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"),
}
_CC_STYLES = ("engage", "pick")


@dataclass(frozen=True)
class MatchupCard:
    title: str                   # "PLAN DE VOIE : DARIUS"
    lines: tuple[str, ...]       # 1-2 lane plan lines ("Action : raison" style)
    jungle: str | None = None    # line about the enemy jungler (first gank)
    gank_side: str | None = None  # "top" | "bot": side of his probable first gank

    @property
    def subtitle(self) -> str:
        return self.lines[0] if self.lines else (self.jungle or "")


def _profile(alias: Any) -> Any:
    from treeaicoach import meta
    return meta.profile(alias)


def _name(p: Any) -> str:
    return str(getattr(p, "champion_name", "") or getattr(p, "champion_alias", "") or "?")


def _early_threat(aliases: list[str]) -> float:
    """How dangerous a lane is to gank WITH early: early curve + crowd control styles."""
    s = 0.0
    for a in aliases:
        m = _profile(a)
        s += {"early": 1.0, "mid": 0.5, "late": 0.0}.get(m.curve, 0.5)
        s += 0.5 * sum(1 for st in _CC_STYLES if m.has(st))
        s += 0.25 * max(0, int(m.ratings[2]) - 2)          # control rating
    return s


def probable_gank_side(enemies: list[Any]) -> str | None:
    """``"top"`` / ``"bot"``: enemy side lane the jungler most likely plays around first (strong
    early lanes with crowd control are the easiest to gank with), None when it is a toss-up."""
    try:
        top = [p.champion_alias for p in enemies if str(getattr(p, "position", "")).upper() == "TOP"]
        bot = [p.champion_alias for p in enemies if str(getattr(p, "position", "")).upper() in ("BOTTOM", "UTILITY")]
        if not top or not bot:
            return None
        t, b = _early_threat(top), _early_threat(bot) / max(1, len(bot)) * 1.3   # 2v2: one easier kill
        if abs(t - b) < 0.5:
            return None
        return "top" if t > b else "bot"
    except Exception:
        return None


MAX_WORDS = 12


def fit(line: str | None) -> str | None:
    """At most :data:`MAX_WORDS` words: the "(probable ...)" detail goes first, then the tail."""
    if not line:
        return line
    if len(line.split()) > MAX_WORDS and " (" in line:
        line = line.split(" (", 1)[0]
    words = line.split()
    return " ".join(words[:MAX_WORDS]) if len(words) > MAX_WORDS else line


def lane_lines(me_alias: str, opp_alias: str, opp_name: str) -> list[str]:
    """1-2 lane plan lines (plain French, "action : raison"). Pure."""
    me, op = _profile(me_alias), _profile(opp_alias)
    out: list[str] = []
    if me.known and op.known:
        d = CURVE_RANK.get(me.curve, 1) - CURVE_RANK.get(op.curve, 1)
        if d > 0:
            out.append("Joue agressif avant le niveau 6 : tu es plus fort tôt")
        elif d < 0 and op.curve == "early":
            out.append(f"Farme prudemment jusqu'au niveau 6 : {opp_name} est plus fort tôt")
        elif d < 0:
            out.append(f"Prends l'avantage tôt : {opp_name} devient fort plus tard")
        if op.ranged and not me.ranged:
            out.append("Reste derrière tes sbires : il a plus de portée que toi")
        elif me.ranged and not op.ranged:
            out.append("Tape-le quand il prend un sbire : tu as plus de portée")
        elif int(op.ratings[3]) >= 3 and int(me.ratings[3]) <= 2:
            out.append(f"Garde une balise dans ta rivière : {opp_name} est très mobile")
        elif op.has("poke"):
            out.append(f"Ne reste pas en face de ses sorts : {opp_name} harcèle de loin")
    if not out:
        out.append("Tue vite la première vague : le premier niveau 2 gagne l'échange")
    return out[:2]


def jungle_line(jungler: Any, gank_side: str | None, my_role: str | None) -> str | None:
    if jungler is None:
        return None
    m = _profile(getattr(jungler, "champion_alias", ""))
    name = _name(jungler)
    where = f" (probable {SIDE_FR[gank_side]})" if gank_side in SIDE_FR else ""
    if m.curve == "early" or m.has("dive") or m.has("engage"):
        return f"Balise ta rivière avant 2:30 : {name} ganke tôt{where}"
    if m.curve == "late":
        return f"Joue ta voie : {name} farme surtout, premier gank vers 3:00{where}"
    return f"Surveille ta rivière vers 2:30 : premier gank de {name}{where}"


def jungler_first_gank(enemies: list[Any]) -> tuple[str, str] | None:
    """For MY jungle: ``(lane, enemy name)`` of the easiest first gank (low mobility, no escape)."""
    best = None
    for p in enemies:
        pos = str(getattr(p, "position", "")).upper()
        lane = {"TOP": "top", "MIDDLE": "mid", "BOTTOM": "bot"}.get(pos)
        if lane is None:
            continue
        m = _profile(p.champion_alias)
        score = -int(m.ratings[3]) + (1 if m.ranged else 0) + (0.5 if m.curve == "late" else 0)
        if best is None or score > best[0]:
            best = (score, lane, _name(p))
    return (best[1], best[2]) if best else None


def matchup_card(game: Any, my_role: str | None, opp_alias: str | None) -> MatchupCard | None:
    """The game-start card (see the module docstring), None without data. Never raises."""
    try:
        me = getattr(game, "me", None)
        if me is None:
            return None
        enemies = list(getattr(game, "enemies", None) or [])
        jg = game.enemy_jungler() if hasattr(game, "enemy_jungler") else None
        role = str(my_role or getattr(me, "position", "") or "").upper()
        side = probable_gank_side(enemies)
        if role == "JUNGLE":
            fg = jungler_first_gank(enemies)
            lines = [f"Premier gank {fg[0]} : {fg[1]} a du mal à s'échapper"] if fg else []
            if jg is not None:
                d = CURVE_RANK.get(_profile(me.champion_alias).curve, 1) - CURVE_RANK.get(
                    _profile(jg.champion_alias).curve, 1)
                if d < 0:
                    lines.append(f"Évite {_name(jg)} au début : il est plus fort tôt")
                elif d > 0:
                    lines.append(f"Envahis tôt si tes voies suivent : {_name(jg)} est faible au début")
            if not lines:
                return None
            return MatchupCard("PLAN DE JUNGLE", tuple(fit(x) for x in lines[:2]), None, None)
        opp = None
        if opp_alias:
            opp = next((p for p in enemies if str(p.champion_alias).lower() == str(opp_alias).lower()), None)
        if opp is None:
            return None
        name = _name(opp)
        lines = lane_lines(me.champion_alias, opp.champion_alias, name)
        my_side = {"TOP": "top", "BOTTOM": "bot", "UTILITY": "bot"}.get(role)
        jl = jungle_line(jg, side, role)
        if jl and my_side and side == my_side:
            jl = jl.replace(" ganke tôt", " viendra probablement ici").replace(f" (probable {SIDE_FR[side]})", "")
        return MatchupCard(f"PLAN DE VOIE : {name.upper()}"[:40], tuple(fit(x) for x in lines), fit(jl), side)
    except Exception:
        log.debug("matchup_card failed", exc_info=True)
        return None


def map_fields(state: Any) -> dict[str, Any]:
    """:class:`treeaicoach.tips.TipContext` fields from :class:`treeaicoach.phase.MapState`."""
    out: dict[str, Any] = {}
    try:
        if state is None:
            return out
        mine = getattr(state, "my_team", None)
        if mine not in ("ORDER", "CHAOS"):
            return out
        sp = state.soul_point() if hasattr(state, "soul_point") else None
        if sp is not None and getattr(state, "soul_team", None) is None:
            out["soul"] = "us" if sp == mine else "them"
        bt, bl = getattr(state, "baron_team", None), float(getattr(state, "baron_left", 0.0) or 0.0)
        if bt and bl > 0:
            out["baron_buff_s" if bt == mine else "enemy_baron_s"] = bl
        et, el = getattr(state, "elder_team", None), float(getattr(state, "elder_left", 0.0) or 0.0)
        if et and el > 0:
            out["elder_buff_s" if et == mine else "enemy_elder_s"] = el
    except Exception:
        log.debug("map_fields failed", exc_info=True)
    return out


__all__ = ["MatchupCard", "matchup_card", "lane_lines", "jungle_line", "probable_gank_side",
           "jungler_first_gank", "map_fields", "OBJ_ROLES"]
