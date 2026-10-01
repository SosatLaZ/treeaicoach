"""Champion profiles from the bundled ``assets/champion_meta.json`` (``tools/fetch_meta.py``).

Schema 2 (patch 26.19+): one profile per champion of the live Data Dragon version, every field
with its source (``src``): Riot Data Dragon (stats, tags), the Riot client data (official
playstyle ratings, damage / attack type, recommended positions), Meraki Analytics / League of
Legends Wiki (class roles) and the TreeAI curation (power curve, level-6 spike, waveclear,
splitpush, sustain, lane class). See ``tools/fetch_meta.py`` for the details.

Used by the fight power estimate (:mod:`treeaicoach.fight`), the build advice
(:mod:`treeaicoach.itemization`), the lane plan (:mod:`treeaicoach.game_plan`), the champion
select card and the macro rules. A champion released after the build gets a rule-derived profile
from the runtime Data Dragon refresh (:mod:`treeaicoach.game_data`: tags + base stats,
``source == "rule"``); an unknown alias gets a neutral profile. Pure Python, cached, never raises.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Iterator

log = logging.getLogger(__name__)

_DATA: dict[str, dict] | None = None
_HEADER: dict[str, Any] = {}
_LOWER: dict[str, str] = {}
_RULE_CACHE: dict[str, "ChampMeta"] = {}
_lock = threading.Lock()

#: Lane classes used by the matchup tips (:mod:`treeaicoach.game_plan`, ``assets/matchups.json``).
LANE_CLASSES: tuple[str, ...] = ("juggernaut", "diver", "skirmisher", "tank", "assassin", "mage", "marksman",
                                 "enchanter", "engage")
_TAG_LANE = {"Fighter": "skirmisher", "Tank": "tank", "Mage": "mage", "Assassin": "assassin",
             "Marksman": "marksman", "Support": "enchanter"}
_TAG_POS = {"Fighter": "TOP", "Tank": "TOP", "Mage": "MIDDLE", "Assassin": "MIDDLE", "Marksman": "BOTTOM",
            "Support": "UTILITY"}
_TAG_CLS = {"Tank": ("TANK", "VANGUARD"), "Fighter": ("FIGHTER", "JUGGERNAUT"), "Mage": ("MAGE", "BURST"),
            "Assassin": ("ASSASSIN",), "Marksman": ("MARKSMAN",), "Support": ("SUPPORT", "ENCHANTER")}


@dataclass(frozen=True)
class ChampMeta:
    alias: str
    damage: str = "P"                      # "P" physical | "M" magic | "X" mixed
    ranged: bool = False
    attack_range: int = 125
    classes: tuple[str, ...] = ()          # class roles (VANGUARD, CATCHER, MARKSMAN...)
    positions: tuple[str, ...] = ()        # Live Client names (TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY), usual first
    ratings: tuple[int, int, int, int, int] = (2, 2, 2, 2, 1)   # damage, toughness, control, mobility, utility
    style: tuple[str, ...] = ()
    curve: str = "mid"                     # "early" | "mid" | "late"
    hp: int = 600
    hp_per_level: int = 100
    known: bool = False
    # schema 2
    name: str = ""                         # French name
    key: int = 0
    subclass: str = ""                     # primary subclass ("juggernaut", "catcher"...)
    lane_class: str = ""                   # one of LANE_CLASSES
    spike6: int = 2                        # level-6 power spike 1..3
    mobility: int = 2                      # 1..3
    cc: int = 1                            # crowd control 0..3
    sustain: int = 1                       # 1..3
    waveclear: int = 2                     # 1..3
    splitpush: int = 1                     # 1..3
    difficulty: int = 2                    # 1..3
    move_speed: int = 340
    source: str = ""                       # "data" (bundled profile) | "rule" (derived at runtime) | ""
    female: bool = False                   # French grammatical gender of the name ("elle est forte")

    def has(self, style: str) -> bool:
        return style in self.style

    @property
    def main_position(self) -> str:
        return self.positions[0] if self.positions else ""

    def curve_factor(self, game_time: float) -> float:
        """Relative strength from the power curve at ``game_time`` (seconds): 0.88..1.12."""
        try:
            m = float(game_time) / 60.0
        except (TypeError, ValueError):
            return 1.0
        if self.curve == "early":
            return 1.10 if m < 14 else 1.0 if m < 25 else 0.90
        if self.curve == "late":
            return 0.90 if m < 14 else 1.0 if m < 25 else 1.12
        return 1.0


def _load() -> dict[str, dict]:
    global _DATA, _LOWER, _HEADER
    with _lock:
        if _DATA is None:
            data: dict[str, dict] = {}
            header: dict[str, Any] = {}
            try:
                from treeaicoach.paths import asset_path

                raw = json.loads(asset_path("champion_meta.json").read_text(encoding="utf-8"))
                champs = raw.get("champions") if isinstance(raw, dict) else None
                if isinstance(champs, dict):
                    data = {str(k): v for k, v in champs.items() if isinstance(v, dict)}
                if isinstance(raw, dict):
                    header = {k: raw.get(k) for k in ("schema", "version", "patch", "generated") if k in raw}
            except Exception:
                log.warning("Champion meta unavailable (assets/champion_meta.json)", exc_info=True)
            _DATA = data
            _HEADER = header
            _LOWER = {k.lower(): k for k in data}
        return _DATA


def _int(x: Any, default: int, lo: int = 0, hi: int = 3) -> int:
    try:
        return max(lo, min(hi, int(x)))
    except (TypeError, ValueError):
        return default


def _from_row(key: str, d: dict) -> ChampMeta:
    r = tuple(_int(x, 2) for x in (d.get("r") or (2, 2, 2, 2, 1)))[:5]
    if len(r) < 5:
        r = r + (1,) * (5 - len(r))
    lane = str(d.get("lane") or "")
    return ChampMeta(
        alias=key, damage=str(d.get("dmg") or "P"), ranged=d.get("rng") == "R",
        attack_range=_int(d.get("ar"), 125, 0, 2000), classes=tuple(str(x) for x in d.get("cls") or ()),
        positions=tuple(str(x) for x in d.get("pos") or ()), ratings=r,  # type: ignore[arg-type]
        style=tuple(str(x) for x in d.get("style") or ()), curve=str(d.get("curve") or "mid"),
        hp=_int(d.get("hp"), 600, 1, 5000), hp_per_level=_int(d.get("hpl"), 100, 0, 1000), known=True,
        name=str(d.get("name") or key), key=_int(d.get("key"), 0, 0, 100000), subclass=str(d.get("class") or ""),
        lane_class=lane if lane in LANE_CLASSES else "", spike6=_int(d.get("spike6"), 2, 1, 3),
        mobility=_int(d.get("mob", r[3]), 2, 1, 3), cc=_int(d.get("cc", r[2]), 1, 0, 3),
        sustain=_int(d.get("sus"), 1, 1, 3), waveclear=_int(d.get("wave"), 2, 1, 3),
        splitpush=_int(d.get("split"), 1, 1, 3), difficulty=_int(d.get("diff"), 2, 1, 3),
        move_speed=_int(d.get("ms"), 340, 0, 1000), source="data", female=d.get("g") == "f")


def _rule_profile(alias: str) -> ChampMeta | None:
    """Profile of a champion of the runtime Data Dragon data missing from the bundled profiles
    (released after the build): tags + base stats, everything else neutral. None if unknown."""
    a = alias.casefold()
    if a in _RULE_CACHE:
        return _RULE_CACHE[a]
    out = None
    try:
        from treeaicoach import game_data

        for c in game_data.champions_data().get("champions") or []:
            if str(c.get("alias") or "").casefold() != a:
                continue
            tags = [str(t) for t in c.get("tags") or ()]
            st = c.get("st") if isinstance(c.get("st"), dict) else {}
            info = st.get("info") if isinstance(st.get("info"), list) else []
            ar = _int(st.get("ar"), 0, 0, 2000)
            ranged = ar >= 300 if ar else (tags[:1] in (["Marksman"], ["Mage"], ["Support"]))
            magic = len(info) >= 3 and float(info[2] or 0) > float(info[0] or 0)
            tag0 = tags[0] if tags else "Fighter"
            out = ChampMeta(alias=str(c.get("alias")), damage="M" if magic or tag0 in ("Mage", "Support") else "P",
                            ranged=ranged, attack_range=ar or (550 if ranged else 125),
                            classes=_TAG_CLS.get(tag0, ()), positions=(_TAG_POS.get(tag0, "TOP"),),
                            hp=_int(st.get("hp"), 600, 1, 5000), hp_per_level=_int(st.get("hpl"), 100, 0, 1000),
                            known=True, name=str(c.get("name_fr") or c.get("alias")), key=_int(c.get("key"), 0, 0, 100000),
                            subclass=_TAG_CLS.get(tag0, ("",))[-1].lower(), lane_class=_TAG_LANE.get(tag0, "skirmisher"),
                            move_speed=_int(st.get("ms"), 340, 0, 1000), source="rule")
            break
    except Exception:
        log.debug("rule profile failed for %r", alias, exc_info=True)
    _RULE_CACHE[a] = out          # type: ignore[assignment]
    return out


def profile(alias: Any) -> ChampMeta:
    """Profile of a champion alias ("MonkeyKing"); a neutral profile when unknown."""
    a = str(alias or "")
    try:
        data = _load()
        key = a if a in data else _LOWER.get(a.lower())
        d = data.get(key) if key else None
        if d:
            return _from_row(key or a, d)
        if a:
            rule = _rule_profile(a)
            if rule is not None:
                return rule
        return ChampMeta(alias=a)
    except Exception:
        log.debug("meta.profile failed for %r", a, exc_info=True)
        return ChampMeta(alias=a)


def team_styles(aliases: Any) -> dict[str, int]:
    """Count of each play style in a team (``{"engage": 2, "poke": 1, ...}``)."""
    out: dict[str, int] = {}
    for a in aliases or ():
        for s in profile(a).style:
            out[s] = out.get(s, 0) + 1
    return out


def known_count() -> int:
    return len(_load())


def aliases() -> list[str]:
    """Champion aliases of the bundled profiles (sorted)."""
    return sorted(_load())


def all_profiles() -> Iterator[ChampMeta]:
    for a in aliases():
        yield profile(a)


def header() -> dict[str, Any]:
    """``{"schema", "version", "patch", "generated"}`` of the bundled profiles."""
    _load()
    return dict(_HEADER)


def reset() -> None:
    """Forget the cached tables (after a runtime Data Dragon update / in tests)."""
    global _DATA
    with _lock:
        _DATA = None
        _RULE_CACHE.clear()


try:  # champions released after the build: re-derive their rule profiles after a refresh
    from treeaicoach import game_data as _game_data

    _game_data.add_listener(lambda: _RULE_CACHE.clear())
except Exception:  # pragma: no cover - defensive
    log.debug("game_data listener not registered", exc_info=True)


__all__ = ["ChampMeta", "profile", "team_styles", "known_count", "aliases", "all_profiles", "header",
           "LANE_CLASSES", "reset"]
