"""FIRST GANK forecast: where the enemy jungler ends his first clear (~3:00-3:45), before he shows up.

A 2026 jungler most often starts on his BLUE buff and ends his first clear on the other half of the
map (his red side), where he ganks the lane next to it between ~3:00 and ~3:45. The minimap almost
never shows his start (fog), so the forecast uses the map itself: the enemy red side is the TOP half
when they are CHAOS (red buff at uv 0.48, 0.27) and the BOTTOM half when they are ORDER (0.52, 0.73).

Validated on the 20 LCU truth files of the user's games (per-minute true positions; numbers kept in
:data:`VALIDATION`): the enemy jungler started on his blue buff in 15 / 20 games; when my lane is on
his red side, he was on my half of the map at 3:00 or 4:00 in 6 / 7 games (86 %), against 6 / 11
(55 %) for a lane on his blue side, so only the red side lane gets the call. Live minimap sightings
of his start were checked too and are too rare / wrong (4 right out of 9 seen) to refine it: unused.

ONE card line (no voice: it is a forecast, the danger beep / gc_jungler_unseen speak when he really
comes), once per game, only while laning in my lane, never when he is dead or SEEN on the other
half. Riot policy: map knowledge + public Live Client roles only. Pure; never raises.
"""

from __future__ import annotations

import logging
from typing import Any

from treeaicoach import macro as M

log = logging.getLogger(__name__)

KIND = "gc_first_gank"
#: enemy team -> map half of its red buff (where its jungler ends a blue-start clear)
RED_SIDE = {"CHAOS": "top", "ORDER": "bot"}
WINDOW_FROM_GT = 160.0          # 2:40: the line comes ~20 s before he can arrive ...
WINDOW_TO_GT = 225.0            # ... and is never NEW after 3:45 (he has ganked or gone)
KEEP_TO_GT = 235.0
ROLES = frozenset({"TOP", "BOTTOM", "UTILITY"})
VALIDATION = {"games": 20, "blue_start": 15, "red_side_hit": (6, 7), "blue_side_hit": (6, 11)}


def danger_lane(my_team: Any) -> str | None:
    """The lane ("top" / "bot") next to the enemy red buff, None when my team is unknown."""
    other = M.OTHER.get(str(my_team or "").upper())
    return RED_SIDE.get(other or "")


def rule_first_gank(ctx: M.MacroCtx) -> M.GeniusCall | None:
    """"Reste près de ta tour : Lee Sin arrive de ton côté" at 2:40-3:45 when my lane is next to the
    enemy red buff (see the module docstring for the numbers)."""
    try:
        if not ctx.me_alive or ctx.in_base or ctx.role not in ROLES or not ctx.jungler_alias:
            return None
        if not (WINDOW_FROM_GT <= ctx.gt < (KEEP_TO_GT if ctx.keep else WINDOW_TO_GT)):
            return None
        lane = M._my_lane(ctx)
        if lane is None or lane != danger_lane(ctx.my_team):
            return None
        if ctx.me_uv is None or M._zone_lane(ctx.me_uv) != lane:
            return None
        jl = M.jungler_location(ctx)
        if jl.dead:
            return None
        if jl.conf >= 0.25 and jl.side not in (None, lane, "mid"):
            return None                                  # seen on the other half: the forecast is wrong
        name = jl.name or "leur jungler"
        text = f"Reste près de ta tour : {name} arrive de ton côté"
        if len(text) > 60:
            text = "Reste près de ta tour : leur jungler arrive de ton côté"
        return M._call(KIND, f"{KIND}:{lane}", "PREMIER GANK", text,
                       "Leur jungler finit le plus souvent son premier tour de ton côté, entre 3:00 et 3:45.",
                       M._safe_uv(ctx), tier="basic", score=0.45, priority=70, color="gold", life=20.0,
                       label="TA TOUR", factors=("premier tour du jungler ennemi", f"côté {lane}"))
    except Exception:
        log.debug("first gank rule failed", exc_info=True)
        return None


RULES = (rule_first_gank,)
