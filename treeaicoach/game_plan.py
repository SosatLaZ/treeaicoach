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
* :func:`lane_lines` - 2-3 lane tips from the matchup knowledge base ``assets/matchups.json``
  (champion pair > tips against that champion > lane class pair > rules from the champion
  profiles: power curve, level-6 spike, range, mobility, poke, sustain > tips against the lane
  class). Every line is an instruction, verb first, "action : raison", 12 words at most.

Nothing about enemy cooldowns or summoner spells. Pure Python, never raises.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

MATCHUPS_FILE = "matchups.json"
_MATCHUPS: dict[str, Any] | None = None
_mlock = threading.Lock()


def matchups() -> dict[str, Any]:
    """The matchup knowledge base (``assets/matchups.json``, cached; ``{}`` when unavailable)."""
    global _MATCHUPS
    with _mlock:
        if _MATCHUPS is None:
            data: Any = {}
            try:
                from treeaicoach.paths import asset_path

                data = json.loads(asset_path(MATCHUPS_FILE).read_text(encoding="utf-8"))
            except Exception:
                log.warning("Matchup tips unavailable (assets/%s)", MATCHUPS_FILE, exc_info=True)
            _MATCHUPS = data if isinstance(data, dict) else {}
        return _MATCHUPS

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


def rule_lines(me_alias: str, opp_alias: str, opp_name: str) -> list[str]:
    """Lane lines derived from the two champion profiles (curve, level-6 spike, range, mobility,
    poke, sustain). Pure."""
    me, op = _profile(me_alias), _profile(opp_alias)
    out: list[str] = []
    if not (me.known and op.known):
        return out
    d = CURVE_RANK.get(me.curve, 1) - CURVE_RANK.get(op.curve, 1)
    e = "e" if op.female else ""
    if d > 0:
        out.append("Joue agressif avant le niveau 6 : tu es plus fort tôt")
    elif d < 0 and op.curve == "early":
        out.append(f"Farme prudemment jusqu'au niveau 6 : {opp_name} est fort{e} tôt")
    elif d < 0:
        out.append(f"Prends l'avantage tôt : {opp_name} devient fort{e} plus tard")
    if op.spike6 >= 3 and me.spike6 <= 2:
        out.append("Recule à son niveau 6 : son ultime change le combat")
    elif me.spike6 >= 3 and op.spike6 <= 2:
        out.append("Attaque à ton niveau 6 : ton ultime gagne l'échange")
    if op.ranged and not me.ranged:
        out.append(f"Reste derrière tes sbires : {'elle' if op.female else 'il'} a plus de portée que toi")
    elif me.ranged and not op.ranged:
        out.append(f"Frappe quand {'elle' if op.female else 'il'} prend un sbire : tu as plus de portée")
    elif int(op.ratings[3]) >= 3 and int(me.ratings[3]) <= 2:
        out.append(f"Garde une balise dans ta rivière : {opp_name} est très mobile")
    elif op.has("poke"):
        out.append(f"Ne reste pas en face de ses sorts : {opp_name} harcèle de loin")
    if op.sustain >= 3 and me.sustain <= 2:
        out.append(f"Achète un anti-soin tôt : {opp_name} se soigne beaucoup")
    return out


def _fill(lines: Any, opp_name: str, female: bool = False) -> list[str]:
    """Placeholders: ``{opp}`` name, ``{il}`` il / elle, ``{e}`` feminine agreement."""
    out = []
    for line in lines if isinstance(lines, list) else []:
        if isinstance(line, str) and line.strip():
            out.append(line.replace("{opp}", opp_name).replace("{il}", "elle" if female else "il")
                       .replace("{e}", "e" if female else ""))
    return out


def matchup_lines(me_alias: str, opp_alias: str, opp_name: str) -> list[tuple[str, str]]:
    """Every candidate lane line with its tier, most specific first: ``("pair_champion", line)``,
    ``vs_champion``, ``pair_class``, ``rule``, ``vs_class``. Pure, never raises."""
    try:
        kb = matchups()
        me, op = _profile(me_alias), _profile(opp_alias)
        ma, oa = getattr(me, "alias", me_alias), getattr(op, "alias", opp_alias)
        fem = bool(getattr(op, "female", False))
        tiers: list[tuple[str, list[str]]] = [
            ("pair_champion", _fill((kb.get("pair_champion") or {}).get(f"{ma}>{oa}"), opp_name, fem)),
            ("vs_champion", _fill((kb.get("vs_champion") or {}).get(oa), opp_name, fem)),
            ("pair_class", _fill((kb.get("pair_class") or {}).get(f"{me.lane_class}>{op.lane_class}"), opp_name, fem)
             if me.lane_class and op.lane_class else []),
            ("rule", rule_lines(me_alias, opp_alias, opp_name)),
            ("vs_class", _fill((kb.get("vs_class") or {}).get(op.lane_class), opp_name, fem)
             if op.lane_class else []),
        ]
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for tier, lines in tiers:
            for line in lines:
                action, _sep, why = line.partition(" : ")
                keys = {action.strip().casefold(), why.strip().casefold() or line.casefold()}
                if not keys & seen:          # same advice or same reason already given
                    seen |= keys
                    out.append((tier, line))
        return out
    except Exception:
        log.debug("matchup_lines failed", exc_info=True)
        return []


def lane_lines(me_alias: str, opp_alias: str, opp_name: str, n: int = 2) -> list[str]:
    """``n`` (2 by default, 3 for the pre-game card) lane plan lines in plain French, "action :
    raison", the most specific first (see the module doc). Pure."""
    out = [line for _tier, line in matchup_lines(me_alias, opp_alias, opp_name)]
    if not out:
        out.append("Tue vite la première vague : le premier niveau 2 gagne l'échange")
    return out[:max(1, int(n))]


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


def team_card(reading: Any) -> MatchupCard | None:
    """The game-start TEAM plan card from a :class:`treeaicoach.mastermind.Reading`: "PLAN D'ÉQUIPE"
    + our main win condition (an instruction, verb first: card material) + my role in it. The
    subtitle (first line) is what the banner shows. None without a reading. Never raises."""
    try:
        if reading is None:
            return None
        lines = [x for x in reading.team_plan() if x]
        if not lines:
            return None
        first = fit(lines[0]) or lines[0]
        rest = tuple(x for x in lines[1:] if x)
        return MatchupCard("PLAN D'ÉQUIPE", (first,) + rest, None, None)
    except Exception:
        log.debug("team_card failed", exc_info=True)
        return None


def team_plan_lines(game: Any, my_role: str | None = None, opp_alias: str | None = None) -> list[str]:
    """Pre-game / game-start team read in plain French from a Live Client snapshot (mastermind):
    both compositions, the power window, our win conditions, my role. Pure, never raises."""
    try:
        from treeaicoach import mastermind

        r = mastermind.analyze(game, role=my_role, lane_opp=opp_alias)
        return r.lines() if r is not None else []
    except Exception:
        log.debug("team_plan_lines failed", exc_info=True)
        return []


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


__all__ = ["MatchupCard", "matchup_card", "team_card", "team_plan_lines", "lane_lines", "rule_lines", "matchup_lines", "matchups", "jungle_line",
           "probable_gank_side", "jungler_first_gank", "map_fields", "OBJ_ROLES"]
