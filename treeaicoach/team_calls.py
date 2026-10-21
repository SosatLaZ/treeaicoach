"""CALLS D'ÉQUIPE: the few decisive calls a premade team acts on together (Discord voice), 100 % rule based.

Two halves:

* :func:`rule_cross_objective` (planner rule, kind ``gc_cross_obj``, registered in
  :data:`treeaicoach.game_changers.RULES`): 3+ enemies SEEN far from an epic pit (the rest dead or at
  most one unaccounted, never their jungler: no smite steal), nobody of theirs near it, at most one of
  us dead, and their walk to the pit covers ours + most of the take time ->
  "Prends le Baron maintenant : ils sont 4 en bas (31 s)" (team line "Ils sont 4 bot : Baron
  maintenant"). The window is the WALK of the closest seen enemy (straight line, +10 % speed: their
  real path is longer, so it is a floor).
* the TEAM LINE of the decisive calls (:func:`team_line` for a planner call, :func:`director_line`
  for an end-game call of :class:`treeaicoach.phase.EndGameCaller`, :func:`prep_line` 60 s before an
  objective): one short sentence the app sends to the bot Discord as kind ``team_call`` when the
  "Calls d'équipe" switch (``discord_team_calls``, off by default) is on. Personal calls (plates,
  recall, lane) never go to the team channel.

Pure, never raises.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Any

from treeaicoach import geometry
from treeaicoach import macro as M

log = logging.getLogger(__name__)

FAR_R = 0.50                 # an enemy this far from the pit (uv) is "on the other side"
NEAR_R = 0.30                # any enemy seen this close: no call (they sit on it)
SEEN_S = 10.0                # "seen" = visible or last seen at most this long ago
SEEN_KEEP_S = 20.0
MIN_FAR = 3                  # at least this many enemies SEEN far
PATH_K = 1.25                # a real path (lane -> river -> pit) is ~25 % longer than the straight line
ENEMY_SPEED_K = 1.10         # ... and they may walk 10 % faster than our reference speed (floor of their window)
TEAM_N = 3                   # the call needs this many of us at the pit: the 3rd closest of us sets our walk
TAKE_FRAC = 0.70             # they must arrive after we did 70 % of the take (no smite: no steal)
PREP_LEAD_S = (55.0, 65.0)   # the "objective in 60 s" team line, said once per spawn in this window

SIDE_WORD = {"top": "top", "mid": "mid", "bot": "bot"}
OBJ_SAY = {"baron": "Baron", "dragon": "dragon", "elder": "ancestral", "herald": "Héraut", "grubs": "larves"}
#: river side to ward before each objective
PREP_SIDE = {"dragon": "rivière du bas", "baron": "rivière du haut", "grubs": "rivière du haut",
             "herald": "rivière du haut", "elder": "rivière du bas"}
_NUM_FR = {3: "trois", 4: "quatre", 5: "cinq"}
#: planner call kinds that are TEAM calls (the rest is personal)
TEAM_KINDS = frozenset({"gc_cross_obj", "fight_won", "jungler_dead", "free_dragon", "cross_trade", "gc_baron_setup"})


def _uv(p: Any) -> tuple[float, float] | None:
    return M._uv(p)


def _eta(uv: tuple[float, float], pit: tuple[float, float]) -> float:
    return geometry.dist(uv, pit) * PATH_K * geometry.MAP_GAME_UNITS / (M.MOVE_SPEED * ENEMY_SPEED_K)


def team_walk_s(ctx: M.MacroCtx, pit: tuple[float, float]) -> float:
    """Seconds until :data:`TEAM_N` of us (me + the allies seen on the minimap) reach ``pit``
    (path-corrected walk of the 3rd closest; me alone when the allies are unknown)."""
    pts = [ctx.me_uv] if ctx.me_uv is not None and ctx.me_alive else []
    pts += [u for u in (_uv(getattr(a, "uv", None)) for a in (ctx.allies or [])) if u is not None]
    ts = sorted(geometry.dist(p, pit) * PATH_K * geometry.MAP_GAME_UNITS / M.MOVE_SPEED for p in pts)
    if not ts:
        return 0.0
    return ts[min(TEAM_N, len(ts)) - 1]


def _take_s(key: str, gt: float) -> float:
    if key == "baron" and gt >= 1500.0:
        return 20.0
    return M.TAKE_S.get(key, 14.0)


def cross_window(ctx: M.MacroCtx, key: str) -> tuple[int, str, float] | None:
    """``(n enemies seen far, their side, their walk s)`` when ``key``'s pit is free, else None."""
    pit = M.PIT_UV.get(key)
    if pit is None:
        return None
    fresh = M._fresh_enemies(ctx, SEEN_KEEP_S if ctx.keep else SEEN_S)
    if any(geometry.dist(_uv(e.uv), pit) < NEAR_R for e in fresh):  # type: ignore[arg-type]
        return None
    far = [e for e in fresh if geometry.dist(_uv(e.uv), pit) >= FAR_R]  # type: ignore[arg-type]
    if len(far) < (MIN_FAR - (1 if ctx.keep else 0)):
        return None
    far_a = {str(getattr(e, "alias", "") or "").lower() for e in far}
    alive = [str(getattr(p, "champion_alias", "") or "").lower() for p in (getattr(ctx.game, "enemies", None) or [])
             if not bool(getattr(p, "is_dead", False))]
    if not alive:
        return None
    far_a &= set(alive)
    if len(far_a) < (MIN_FAR - (1 if ctx.keep else 0)):
        return None
    unaccounted = [a for a in alive if a not in far_a]
    if len(unaccounted) > (2 if ctx.keep else 1):
        return None
    if ctx.jungler_alias is not None and ctx.jungler_alias in unaccounted:
        return None                                  # their jungler unseen: smite steal / ambush in the fog
    pts = [_uv(e.uv) for e in far if str(getattr(e, "alias", "") or "").lower() in far_a]
    eta = min(_eta(p, pit) for p in pts if p is not None)
    cu = sum(p[0] for p in pts if p) / len(pts)
    cv = sum(p[1] for p in pts if p) / len(pts)
    return len(far_a), M.side_of((cu, cv)), eta


def rule_cross_objective(ctx: M.MacroCtx) -> M.GeniusCall | None:
    """"Ils sont 4 bot : Baron maintenant" / "Ils sont 3 top : dragon maintenant"."""
    if not ctx.me_alive or ctx.in_base or ctx.my_team is None or ctx.gt < 600.0 or ctx.recent_director_call:
        return None
    if len(M._dead_list(ctx, "allies", 5.0)) > 1:
        return None
    for key in ("baron", "elder", "dragon"):
        if key == "baron" and ctx.gt < 1200.0:
            continue
        if not M._obj_up(ctx, key):
            continue
        if key == "dragon" and ctx.phase == "laning" and not M._role_plays(ctx, "dragon"):
            continue
        cw = cross_window(ctx, key)
        if cw is None:
            continue
        n, side, eta = cw
        pit = M.PIT_UV[key]
        need = team_walk_s(ctx, pit) + _take_s(key, ctx.gt) * TAKE_FRAC
        if not ctx.keep and eta < need:
            continue
        side_fr = M.SIDE_FR.get(side, "loin")
        obj = OBJ_SAY[key]
        voice = f"Ils sont {_NUM_FR.get(n, str(n))} {side_fr} : {obj} !"
        score = M._clamp(0.62 + 0.06 * (n - 3) + min(0.2, (eta - need) / 60.0))
        return replace(M._call("gc_cross_obj", f"gc_cross_obj:{key}:{int(ctx.gt // 60)}", M.OBJ_TITLE[key],
                               f"Prends {M.OBJ_LE[key]} maintenant : ils sont {n} {side_fr} ({M._secs(eta)} s)",
                               f"Il leur faut {M._secs(eta)} s pour revenir : le temps de le prendre à 5.",
                               pit, tier="high", score=score, priority=92, color="safe", genius=True,
                               life=max(8.0, min(eta, 25.0)), label=M.OBJ_TITLE[key].rstrip(" !"),
                               factors=(f"{n} ennemis vus {side}", f"retour {M._secs(eta)} s")),
                       voice=voice)
    return None


# ----------------------------------------------------------------------------- team lines (bot Discord)
_N_DEAD = re.compile(r"(\d)\s+ennemis?\s+morts?")
_SECS = re.compile(r"\((\d+) s\)")


def team_line(call: Any, ctx: Any = None) -> str | None:
    """The team sentence of a planner call (:data:`TEAM_KINDS`), None for a personal call."""
    try:
        kind = str(getattr(call, "kind", "") or "")
        if kind not in TEAM_KINDS:
            return None
        text = str(getattr(call, "text", "") or "")
        title = str(getattr(call, "title", "") or "").upper().rstrip(" !")
        obj = {"BARON": "Baron", "DRAGON": "dragon", "ANCESTRAL": "ancestral", "HÉRAUT": "Héraut",
               "LARVES": "larves", "INHIBITEUR": "inhibiteur", "TOUR": "tour"}.get(title)
        secs = _SECS.search(text)
        s = f" ({secs.group(1)} secondes)" if secs else ""
        if kind == "gc_cross_obj":
            m = re.search(r"ils sont (\d) (en haut|en bas|au milieu)", text)
            if m is None or obj is None:
                return None
            side = {"en haut": "top", "en bas": "bot", "au milieu": "mid"}[m.group(2)]
            return f"Ils sont {m.group(1)} {side} : {obj} maintenant !"
        if kind == "fight_won":
            m = _N_DEAD.search(text)
            if obj is None:
                return None
            n = m.group(1) if m else "plusieurs"
            what = {"tour": "la tour", "inhibiteur": "l'inhibiteur"}.get(obj, obj)
            return f"{n} morts : {what} maintenant{s} !"
        if kind == "jungler_dead":
            if obj is None or obj in ("tour", "inhibiteur"):
                return None
            return f"Leur jungler est mort : {obj} maintenant{s} !"
        if kind == "free_dragon":
            if obj not in ("dragon", "ancestral"):
                return None
            m = re.search(r"(\d) ennemis", str(getattr(call, "why", "") or ""))
            head = f"Ils sont {m.group(1)} top" if m else "Leur jungler est top"
            return f"{head} : {obj} gratuit{s} !"
        if kind == "cross_trade":
            if obj not in ("Héraut", "larves"):
                return None
            return f"Leur jungler est bot : {obj} maintenant !"
        if kind == "gc_baron_setup":
            return "On est devant : balises au Baron, puis Baron."
        return None
    except Exception:
        log.debug("team_line failed", exc_info=True)
        return None


def director_line(key: str, text: str = "") -> str | None:
    """The team sentence of an end-game call (phase.EndGameCaller key, prefix urgent: / macro:)."""
    k = str(key or "").split(":", 1)[-1] if str(key or "").startswith(("urgent:", "macro:")) else str(key or "")
    m = re.search(r"(\d+) ennemis? morts", text or "")
    n = f"{m.group(1)} morts" if m else "Ace"
    return {"ace:end": f"{n} : tous au nexus maintenant !",
            "ace:baron": f"{n} : Baron maintenant !",
            "ace:dragon": f"{n} : dragon maintenant !",
            "ace:push": f"{n} : poussez mid et prenez la tour !",
            "carries_dead": "Leurs carrys sont morts : poussez et prenez une tour !",
            "baron_theirs": "Ils ont le Baron : on défend sous tour, pas de combat.",
            "elder_ours:theirs": "Ils ont l'ancestral : on évite le combat."}.get(k)


def prep_line(key: str, remaining: float) -> str | None:
    """"Dragon dans une minute : vision rivière du bas, soyez là." (55-65 s before the spawn)."""
    if key not in PREP_SIDE or remaining is None or not (PREP_LEAD_S[0] <= float(remaining) <= PREP_LEAD_S[1]):
        return None
    name = {"dragon": "Dragon", "baron": "Baron", "grubs": "Larves", "herald": "Héraut", "elder": "Ancestral"}[key]
    return f"{name} dans une minute : vision {PREP_SIDE[key]}, soyez là."


def voice_phrases() -> list[str]:
    """Every PC voice line of :func:`rule_cross_objective` (pre-generated)."""
    out = []
    for n in (3, 4, 5):
        for side in ("en haut", "en bas", "au milieu"):
            for obj in ("Baron", "dragon", "ancestral"):
                out.append(f"Ils sont {_NUM_FR[n]} {side} : {obj} !")
    return out


__all__ = ["rule_cross_objective", "cross_window", "team_line", "director_line", "prep_line", "TEAM_KINDS",
           "voice_phrases"]
