"""Champion "meta" profiles from the bundled ``assets/champion_meta.json`` (tools/fetch_meta.py).

Offline, free data (Meraki Analytics + Riot Data Dragon): damage type, range, class roles,
attribute ratings, play style (engage / pick / poke / burst / dive / sustain / peel /
splitpush) and power curve (early / mid / late). Used by the fight power estimate
(:mod:`treeaicoach.fight`), the build advice (:mod:`treeaicoach.itemization`) and the macro
rules. Unknown champions get a neutral profile. Pure Python, cached, never raises.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

_DATA: dict[str, dict] | None = None
_LOWER: dict[str, str] = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class ChampMeta:
    alias: str
    damage: str = "P"                      # "P" physical | "M" magic | "X" mixed
    ranged: bool = False
    attack_range: int = 125
    classes: tuple[str, ...] = ()          # Meraki roles (VANGUARD, CATCHER, MARKSMAN...)
    positions: tuple[str, ...] = ()
    ratings: tuple[int, int, int, int, int] = (2, 2, 2, 2, 1)   # damage, toughness, control, mobility, utility
    style: tuple[str, ...] = ()
    curve: str = "mid"                     # "early" | "mid" | "late"
    hp: int = 600
    hp_per_level: int = 100
    known: bool = False

    def has(self, style: str) -> bool:
        return style in self.style

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
    global _DATA, _LOWER
    with _lock:
        if _DATA is None:
            data: dict[str, dict] = {}
            try:
                from treeaicoach.paths import asset_path

                raw = json.loads(asset_path("champion_meta.json").read_text(encoding="utf-8"))
                champs = raw.get("champions") if isinstance(raw, dict) else None
                if isinstance(champs, dict):
                    data = {str(k): v for k, v in champs.items() if isinstance(v, dict)}
            except Exception:
                log.warning("Champion meta unavailable (assets/champion_meta.json)", exc_info=True)
            _DATA = data
            _LOWER = {k.lower(): k for k in data}
        return _DATA


def profile(alias: Any) -> ChampMeta:
    """Profile of a champion alias ("MonkeyKing"); a neutral profile when unknown."""
    a = str(alias or "")
    try:
        data = _load()
        key = a if a in data else _LOWER.get(a.lower())
        d = data.get(key) if key else None
        if not d:
            return ChampMeta(alias=a)
        r = tuple(int(x) for x in (d.get("r") or (2, 2, 2, 2, 1)))[:5]
        if len(r) < 5:
            r = r + (1,) * (5 - len(r))
        return ChampMeta(alias=key or a, damage=str(d.get("dmg") or "P"), ranged=d.get("rng") == "R",
                         attack_range=int(d.get("ar") or 125), classes=tuple(d.get("cls") or ()),
                         positions=tuple(d.get("pos") or ()), ratings=r,  # type: ignore[arg-type]
                         style=tuple(d.get("style") or ()), curve=str(d.get("curve") or "mid"),
                         hp=int(d.get("hp") or 600), hp_per_level=int(d.get("hpl") or 100), known=True)
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


__all__ = ["ChampMeta", "profile", "team_styles", "known_count"]
