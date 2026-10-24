"""TEAM BRAIN: personal calls for each FRIEND of the voice channel, addressed by his champion (bot Discord).

The bot already says the gank calls of every friend (ally_gank.py), the friends' matchups and ranks
at game start and the decisive team calls (team_calls.py). :class:`TeamBrain` adds what a good
shot-caller tells each mate, from the same public data the app already reads (Live Client Tab data,
the minimap tracks, the jungler probability map), switch ``discord_team_brain`` (on by default):

* ``opp``: his lane opponent bought something while unseen (jungle_intel recalls = Tab purchases)
  and cannot be back for :data:`OPP_MIN_WINDOW_S` -> "Ahri, ton adversaire est en base : plaque"
  (after :data:`LANING_END_GT`: "pousse ta lane"); for a friend jungler the enemy jungler ->
  "Vi, leur jungler est en base : joue fort". Said within :data:`OPP_FRESH_S` of the purchase only.
* ``jungler``: the enemy jungler is NOT seen (not visible for :data:`JG_UNSEEN_S`, alive) and the
  jungler map (jungler_map.py) gives P(he can be on the friend within 20 s) >= :data:`NEAR_P` for a
  friend standing in a lane -> "Jinx, jungler probablement chez toi" (the bot duo: "Jinx et Lulu,
  jungler probablement chez vous"). Re-armed only once P fell under :data:`NEAR_REARM_P`.
* ``sync``: two of us in the same lane (friends, or a friend and me) both rich (unspent gold >=
  :data:`RICH_GOLD`) or me low (< :data:`LOW_HP`) while the friend is rich -> "Jinx et Thresh, rentrez
  ensemble : assez d'or pour un objet". The other players' gold is hidden by the Live Client: it is
  estimated from what they EARNED since their last purchase seen on Tab (items value up), see
  :func:`earned_estimate` (measured on the LCU timelines of 20 games: the 2-minute income error is
  251 gold RMS, +108 bias corrected, against 869 RMS for a total-gold estimate). The other players'
  HP is not public: "low" is only known for me.
* ``objective``: :data:`PLAN_LEAD_S` before a dragon / Baron / larves / Héraut spawn, ONE team line
  with the roles that play it and the fight calculator of fog_intel (alive players, item gold):
  "Dragon dans 1:00 : bot et jungle, rentrez maintenant" or, when the fight is lost on paper,
  "Dragon dans 1:00 : ils ont 3k d'or d'avance, jouez l'autre côté". Same key as
  team_calls.prep_line (the bot says only one of them).

Budget (the bot's :class:`treeaicoach.discord_bot.CallFilter`, kind ``team_brain``): at most ONE
voice line per friend per 30 s (a gank call on him counts and always goes first), the team line counts
for every friend, every call together under ``CALLS_MAX_PER_MIN``. This module rate-limits its own
rules on top (per friend, per lane, per pair, per spawn) and retries a refused line for a few
seconds only. Replayed on 17 real games (``python tools/team_brain_truth.py <games dir>``: LCU truth
positions + the app's recorded sightings, the 4 allies as friends = worst case), numbers in
:data:`VALIDATION`: 0.55 line / min, never 2 brain lines to one friend within 30 s, never one within
30 s after his gank call, at most 5 calls a minute with the ganks. "Jungler chez toi" came true (he
came within 0.15 in 25 s) 11 / 31 against a 15 % base rate: P(near) alone (>= 0.55) was 23 %, so the
call also needs the map to LOCALISE him (every ally on another lane <= 0.25: 44 % on all ticks).

Pure (no Qt / Discord), analysis thread only, never raises from :meth:`TeamBrain.update`.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

from treeaicoach import geometry

log = logging.getLogger(__name__)

EVERY_S = 1.0                 # the rules run at most once per game second
LANING_END_GT = 840.0         # "plaque" before 14:00, "pousse ta lane" after
OPP_FRESH_S = 8.0             # the purchase was seen this recently (else: old news)
OPP_MIN_WINDOW_S = 12.0       # ... and he cannot be back on the friend for at least this long
OPP_MIN_GT = 180.0
OPP_SPEED_UNITS = 560.0       # his walk out of the fountain, upper bound (fog_intel.BASE_SPEED_UNITS)
NEAR_P = 0.70                 # P(enemy jungler near the friend within 20 s) ...
NEAR_OTHER_MAX = 0.25         # ... while every ally on another lane has at most this (the map localises him)
NEAR_REARM_P = 0.30
JG_UNSEEN_S = 10.0            # the jungler not seen for this long ("only when unseen")
JG_FRIEND_S = 90.0            # one jungler line per friend per 90 s
JG_LANE_S = 45.0              # ... and per lane per 45 s
JG_MIN_GT = 150.0
RICH_GOLD = 1300.0            # postgame_edge.IDLE_GOLD: enough for a component
LOW_HP = 0.35
SYNC_NEAR = 0.18              # the two in the same lane, this close
SYNC_PAIR_S = 150.0
SYNC_MIN_GT = 240.0
SYNC_OBJ_GUARD_S = 45.0       # an objective spawns within this: no "rentrez" (objective line says it)
PLAN_LEAD_S = (56.0, 70.0)    # the objective line window (s before the spawn)
RETRY_S = 2.0                 # a line the bot refused is offered again this often ...
RETRY_FOR_S = 10.0            # ... for this long, then dropped
#: income model since the last purchase (gold / s, per CS, per kill / assist), +bias (fit on 20 games)
GOLD_PASSIVE, GOLD_CS, GOLD_KILL, GOLD_ASSIST, GOLD_SUPPORT, GOLD_BIAS = 2.04, 21.0, 300.0, 150.0, 0.6, 0.9
GOLD_LEFT = 150.0             # gold left after a purchase (the median remainder)
BUY_MIN = 250.0               # item value up by this much = a purchase seen on Tab

OBJ_NAME = {"dragon": "Dragon", "baron": "Baron", "grubs": "Larves", "herald": "Héraut", "elder": "Ancestral"}
#: who goes (team line), by objective: the roles of objective_planner.ROLE_PLAYS in words
OBJ_WHO = {"dragon": "bot et jungle", "grubs": "top et jungle", "herald": "top, mid et jungle",
           "baron": "tout le monde", "elder": "tout le monde"}
OTHER_SIDE = {"dragon": "jouez les larves ou le haut", "grubs": "jouez le dragon ou le bas",
              "herald": "jouez le dragon ou le bas", "baron": "défendez et farmez en sécurité",
              "elder": "défendez sous vos tours"}
VALIDATION: dict = {
    "games": 17, "minutes": 478, "lines": 263, "per_min": 0.55,
    "jungler": {"n": 31, "came": 11, "base_rate": 0.15}, "opp": {"n": 83, "safe_15s": 66},
    "sync": {"n": 65, "both_1000_unspent": 57}, "objective": 84,
    "max_brain_per_friend_30s": 1, "after_own_gank_30s": 0, "max_calls_per_min": 5,
}

_LANE_ROLE = {"TOP": "top", "MIDDLE": "mid", "BOTTOM": "bot", "UTILITY": "bot"}


@dataclass(frozen=True)
class BrainLine:
    """One line for the bot: ``targets`` = the friends it addresses (all friends for a team line)."""

    rule: str                     # "opp" | "jungler" | "sync" | "objective"
    text: str
    key: str
    targets: tuple[str, ...]
    team: bool = False
    t: float = 0.0


def _k(x: Any) -> str:
    return "".join(ch for ch in str(x or "").lower() if ch.isalnum())


def _f(x: Any, default: float | None = None) -> float | None:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _score(p: Any, key: str) -> float:
    s = getattr(p, "scores", None)
    if isinstance(s, Mapping):
        return _f(s.get(key), 0.0) or 0.0
    return _f(getattr(p, key, None), 0.0) or 0.0


def earned_estimate(p: Any, gt: float) -> float:
    """Gold earned since the start (passive, CS, kills, assists, support quest; bias corrected) by
    the player ``p`` (Live Client Player: ``scores`` / ``position``). Only DIFFERENCES are used."""
    g = (GOLD_PASSIVE + GOLD_BIAS) * max(0.0, gt) + GOLD_CS * _score(p, "creepScore") \
        + GOLD_KILL * _score(p, "kills") + GOLD_ASSIST * _score(p, "assists")
    if str(getattr(p, "position", "") or "").upper() == "UTILITY":
        g += GOLD_SUPPORT * max(0.0, gt)
    return g


def _items_value(p: Any) -> float:
    try:
        from treeaicoach.live_edge import _items, item_gold

        return float(item_gold(_items(p)))
    except Exception:
        return 0.0


def _pos(tr: Any) -> tuple[float, float] | None:
    try:
        p = getattr(tr, "position", None)
        p = p() if callable(p) else p
        if p is None:
            return None
        u, v = float(p[0]), float(p[1])
        return (u, v) if math.isfinite(u) and math.isfinite(v) else None
    except Exception:
        return None


def _fountain_eta(team: str | None, uv: tuple[float, float]) -> float | None:
    try:
        from treeaicoach.fog_intel import BASE_SPEED, fountain_eta

        eta = fountain_eta(team, uv)
        return None if eta is None else eta * BASE_SPEED * geometry.MAP_GAME_UNITS / OPP_SPEED_UNITS
    except Exception:
        return None


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " et " + names[-1]


class TeamBrain:
    """Per-game state of the personal calls (see the module docstring)."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._last_gt = -1e9
        self._last_t: float | None = None
        self._opp_done: set[tuple] = set()
        self._jg_friend: dict[str, float] = {}
        self._jg_lane: dict[str, float] = {}
        self._jg_armed: dict[str, bool] = {}
        self._sync_pair: dict[tuple, float] = {}
        self._obj_done: set[str] = set()
        self._buy: dict[str, tuple[float, float]] = {}     # alias -> (items value, earned at that purchase)
        self._pending: dict[str, tuple[BrainLine, float, float]] = {}   # key -> (line, first t, last offered t)
        self.said: list[BrainLine] = []

    # ------------------------------------------------------------------ public
    def ack(self, line: BrainLine, queued: bool) -> None:
        """The bot queued ``line`` (or refused it: it is offered again for :data:`RETRY_FOR_S`)."""
        try:
            if queued:
                self._pending.pop(line.key, None)
                self.said.append(line)
                del self.said[:-200]
                self._mark(line)
        except Exception:
            log.debug("team brain ack failed", exc_info=True)

    def update(self, t: float, gt: float | None, game: Any, tracker: Any, friends: Iterable[str],
               *, recalls: Iterable[tuple[str, float]] = (), jmap: Any = None,
               objectives: Iterable[Any] = ()) -> list[BrainLine]:
        """Lines to offer the bot now (each one at most every :data:`RETRY_S` until acked).
        ``friends``: champion aliases of the voice channel's friends in my team (me excluded);
        ``recalls``: ``(enemy alias lower, engine time)`` of their purchases (jungle_intel);
        ``jmap``: the jungler map state; ``objectives``: objectives.ObjectiveTimers states."""
        try:
            now, g = _f(t), _f(gt)
            if now is None or g is None or game is None or tracker is None:
                return []
            if self._last_t is not None and now < self._last_t - 5.0:
                self.reset()                                     # new game / timeline
            self._last_t = now
            fr = [a for a in (str(x) for x in (friends or ())) if a]
            if not fr or not bool(getattr(game, "is_summoners_rift", True)):
                return []
            self._track_purchases(game, g)
            new: list[BrainLine] = []
            if g - self._last_gt >= EVERY_S:
                self._last_gt = g
                ctx = self._context(now, g, game, tracker, fr)
                if ctx is not None:
                    new += self._rule_objective(ctx, objectives)
                    new += self._rule_opp(ctx, dict(recalls or ()))
                    new += self._rule_jungler(ctx, jmap)
                    new += self._rule_sync(ctx, objectives)
            for ln in new:
                if ln.key not in self._pending:
                    self._pending[ln.key] = (ln, now, -1e9)
            out = []
            for key, (ln, first, last) in list(self._pending.items()):
                if now - first > RETRY_FOR_S:
                    del self._pending[key]
                elif now - last >= RETRY_S:
                    self._pending[key] = (ln, first, now)
                    out.append(ln)
            return out
        except Exception:
            log.exception("TeamBrain.update failed")
            return []

    # ------------------------------------------------------------------ context
    def _context(self, now: float, gt: float, game: Any, tracker: Any, friends: list[str]) -> Any:
        me = getattr(game, "me", None)
        team = geometry.normalize_team(getattr(me, "team", None))
        if team is None:
            return None
        enemy_team = "CHAOS" if team == "ORDER" else "ORDER"
        players = {_k(getattr(p, "champion_alias", "")): p for p in list(getattr(game, "allies", None) or [])}
        tracks = {}
        for tr in (tracker.allies(visible_only=False) if hasattr(tracker, "allies") else []):
            if getattr(tr, "alias", None):
                tracks[_k(tr.alias)] = tr
        mt = tracker.me() if hasattr(tracker, "me") else None
        mates = []                               # (alias key, champion name, player, pos, is_me)
        for a in friends:
            k = _k(a)
            p, tr = players.get(k), tracks.get(k)
            if p is None or bool(getattr(p, "is_dead", False)) or k == _k(getattr(me, "champion_alias", "")):
                continue
            pos = _pos(tr) if tr is not None and now - (_f(getattr(tr, "last_seen", now), now) or now) <= 3.0 else None
            mates.append((k, str(getattr(p, "champion_name", "") or a), p, pos, False))
        me_pos = _pos(mt) if mt is not None else None
        enemies = list(getattr(game, "enemies", None) or [])
        etracks = {}
        for tr in (tracker.enemies(visible_only=False) if hasattr(tracker, "enemies") else []):
            if getattr(tr, "alias", None):
                etracks[_k(tr.alias)] = tr
        return SimpleNamespace(t=now, gt=gt, game=game, me=me, team=team, enemy_team=enemy_team, mates=mates,
                               me_pos=me_pos, enemies=enemies, etracks=etracks,
                               friends=tuple(m[0] for m in mates), all_friends=tuple(_k(a) for a in friends))

    @staticmethod
    def _out_of_base(pos: tuple[float, float] | None, team: str) -> bool:
        if pos is None:
            return False
        z = geometry.classify_zone(*pos)
        return not (geometry.is_base(z) and geometry.zone_owner(z) == team)

    def _unseen_since(self, ctx: Any, alias: str, t0: float) -> bool:
        tr = ctx.etracks.get(_k(alias))
        if tr is None:
            return True
        if bool(getattr(tr, "visible", False)):
            return False
        ls = _f(getattr(tr, "last_seen", None))
        return ls is None or ls <= t0 + 0.5

    # ------------------------------------------------------------------ gold
    def _track_purchases(self, game: Any, gt: float) -> None:
        for p in list(getattr(game, "allies", None) or []):
            k = _k(getattr(p, "champion_alias", ""))
            if not k:
                continue
            val = _items_value(p)
            prev = self._buy.get(k)
            if prev is None:
                if gt < 150.0:                     # the starter items: unspent ~ 0 now
                    self._buy[k] = (val, earned_estimate(p, gt))
                else:
                    self._buy[k] = (val, float("nan"))     # app started late: unknown until a purchase
            elif val >= prev[0] + BUY_MIN:
                self._buy[k] = (val, earned_estimate(p, gt))
            elif val < prev[0]:
                self._buy[k] = (val, prev[1])     # sold / consumed: keep the anchor

    def unspent(self, p: Any, gt: float) -> float | None:
        """Estimated unspent gold of the ally ``p`` (None before his first purchase seen)."""
        b = self._buy.get(_k(getattr(p, "champion_alias", "")))
        if b is None or not math.isfinite(b[1]):
            return None
        return GOLD_LEFT + earned_estimate(p, gt) - b[1]

    # ------------------------------------------------------------------ rules
    def _rule_objective(self, ctx: Any, objectives: Iterable[Any]) -> list[BrainLine]:
        out = []
        for o in objectives or ():
            key = str(getattr(o, "key", "") or "")
            rem = _f(getattr(o, "remaining", None))
            if key not in OBJ_NAME or getattr(o, "alive", False) or rem is None:
                continue
            if not (PLAN_LEAD_S[0] <= rem <= PLAN_LEAD_S[1]):
                continue
            spawn = int(round(_f(getattr(o, "next_spawn", None), ctx.gt + rem) or 0.0))
            uid = f"prep:{key}:{spawn}"
            if uid in self._obj_done:
                continue
            name = OBJ_NAME[key]
            odds = None
            try:
                from treeaicoach.fog_intel import _k as kfmt, fight_odds

                odds = fight_odds(SimpleNamespace(game=ctx.game))
            except Exception:
                kfmt = None
            if odds is not None and odds.get("verdict") == "lose" and kfmt is not None and odds["gold"] <= -1000:
                text = f"{name} dans 1:00 : ils ont {kfmt(-odds['gold'])[1:]} d'or d'avance, {OTHER_SIDE[key]}"
            else:
                text = f"{name} dans 1:00 : {OBJ_WHO[key]}, rentrez maintenant"
            out.append(BrainLine("objective", text, uid, ctx.all_friends, team=True, t=ctx.t))
        return out

    def _opponents(self, ctx: Any, role: str) -> list[Any]:
        if role in ("BOTTOM", "UTILITY"):
            roles = ("BOTTOM", "UTILITY")
        else:
            roles = (role,)
        return [e for e in ctx.enemies if str(getattr(e, "position", "") or "").upper() in roles]

    def _rule_opp(self, ctx: Any, recalls: dict) -> list[BrainLine]:
        if ctx.gt < OPP_MIN_GT:
            return []
        rec = {_k(a): _f(tt) for a, tt in recalls.items()}
        found: dict[str, list] = {}               # lane -> [(friend key, name, based aliases)]
        for k, name, p, pos, _ in ctx.mates:
            role = str(getattr(p, "position", "") or "").upper()
            if not role or pos is None or not self._out_of_base(pos, ctx.team):
                continue
            lane = _LANE_ROLE.get(role)
            if lane is not None and geometry.lane_of(geometry.classify_zone(*pos)) != lane:
                continue                          # "plaque" only for a friend standing in his lane
            based = []
            for e in self._opponents(ctx, role):
                a = _k(getattr(e, "champion_alias", ""))
                t0 = rec.get(a)
                if t0 is None or not (0.0 <= ctx.t - t0 <= OPP_FRESH_S) or bool(getattr(e, "is_dead", False)):
                    continue
                if not self._unseen_since(ctx, a, t0):
                    continue
                eta = _fountain_eta(ctx.enemy_team, pos)
                if eta is None or eta - (ctx.t - t0) < OPP_MIN_WINDOW_S:
                    continue
                based.append(f"{a}:{int(t0)}")
            if based:
                found.setdefault(lane or f"jg:{k}", []).append((k, name, tuple(sorted(based))))
        out = []
        for lane, who in found.items():
            uid = ("opp", lane) + tuple(sorted({b for _, _, bs in who for b in bs}))
            if uid in self._opp_done:
                continue
            self._opp_done.add(uid)
            n_based = len({b for _, _, bs in who for b in bs})
            names = [n for _, n, _ in who][:2]
            todo = "plaque" if ctx.gt < LANING_END_GT else "pousse ta lane"
            if lane.startswith("jg:"):
                text = f"{names[0]}, leur jungler est en base : joue fort"
            elif len(names) > 1:
                todo = "plaquez" if ctx.gt < LANING_END_GT else "poussez votre lane"
                what = "vos deux adversaires sont" if n_based >= 2 else "un de vos adversaires est"
                text = f"{_join(names)}, {what} en base : {todo}"
            else:
                what = "tes deux adversaires sont" if n_based >= 2 else "ton adversaire est"
                text = f"{names[0]}, {what} en base : {todo}"
            out.append(BrainLine("opp", text, "|".join(uid), tuple(k for k, _, _ in who[:2]), t=ctx.t))
        return out

    def _rule_jungler(self, ctx: Any, jmap: Any) -> list[BrainLine]:
        if jmap is None or ctx.gt < JG_MIN_GT or bool(getattr(jmap, "visible", False)) \
                or bool(getattr(jmap, "dead", False)):
            return []
        grid = getattr(jmap, "grid", None)
        if grid is None:
            return []
        jg = next((e for e in ctx.enemies if str(getattr(e, "position", "") or "").upper() == "JUNGLE"), None)
        if jg is None or bool(getattr(jg, "is_dead", False)):
            return []
        jtr = ctx.etracks.get(_k(getattr(jg, "champion_alias", "")))
        if jtr is not None and (bool(getattr(jtr, "visible", False))
                                or ctx.t - (_f(getattr(jtr, "last_seen", None), -1e9) or -1e9) < JG_UNSEEN_S):
            return []
        from treeaicoach.jungler_map import JunglerMap

        laners = []                               # (key or "", name, lane, P(near)); "" = me
        people = [(k, name, pos) for k, name, p, pos, _ in ctx.mates
                  if pos is not None and str(getattr(p, "position", "") or "").upper() != "JUNGLE"]
        if ctx.me_pos is not None:
            people.append(("", "", ctx.me_pos))
        for k, name, pos in people:
            lane = geometry.lane_of(geometry.classify_zone(*pos))
            if lane is None:
                if k:
                    self._jg_armed[k] = True
                continue
            try:
                laners.append((k, name, lane, float(grid[JunglerMap.near_mask(pos, ctx.gt)].sum())))
            except Exception:
                continue
        by_lane: dict[str, list[tuple[str, str]]] = {}
        for k, name, lane, pn in laners:
            if not k:
                continue
            if pn < NEAR_REARM_P:
                self._jg_armed[k] = True
            other = [q for _k2, _n, ln, q in laners if ln != lane]
            if pn < NEAR_P or not other or max(other) > NEAR_OTHER_MAX:
                continue                          # nobody elsewhere to compare: the map does not localise him
            if not self._jg_armed.get(k, True) or ctx.t - self._jg_friend.get(k, -1e9) < JG_FRIEND_S:
                continue
            by_lane.setdefault(lane, []).append((k, name))
        out = []
        for lane, who in by_lane.items():
            if ctx.t - self._jg_lane.get(lane, -1e9) < JG_LANE_S:
                continue
            for k, _ in who:
                self._jg_armed[k] = False
                self._jg_friend[k] = ctx.t
            self._jg_lane[lane] = ctx.t
            names = [n for _, n in who][:2]
            tail = "jungler probablement chez vous" if len(names) > 1 else "jungler probablement chez toi"
            out.append(BrainLine("jungler", f"{_join(names)}, {tail}", f"jungler:{lane}:{int(ctx.t)}",
                                 tuple(k for k, _ in who[:2]), t=ctx.t))
        return out

    def _rule_sync(self, ctx: Any, objectives: Iterable[Any]) -> list[BrainLine]:
        if ctx.gt < SYNC_MIN_GT:
            return []
        for o in objectives or ():
            rem = _f(getattr(o, "remaining", None))
            if not getattr(o, "alive", False) and rem is not None and 0.0 <= rem <= SYNC_OBJ_GUARD_S:
                return []
        rich = []
        for k, name, p, pos, _ in ctx.mates:
            if pos is None or not self._out_of_base(pos, ctx.team):
                continue
            g = self.unspent(p, ctx.gt)
            if g is not None and g >= RICH_GOLD:
                rich.append((k, name, pos, g))
        out = []
        used: set[str] = set()
        for i, a in enumerate(rich):
            for b in rich[i + 1:]:
                if a[0] in used or b[0] in used or geometry.dist(a[2], b[2]) > SYNC_NEAR:
                    continue
                pair = tuple(sorted((a[0], b[0])))
                if ctx.t - self._sync_pair.get(pair, -1e9) < SYNC_PAIR_S:
                    continue
                self._sync_pair[pair] = ctx.t
                used |= {a[0], b[0]}
                out.append(BrainLine("sync", f"{a[1]} et {b[1]}, rentrez ensemble : assez d'or pour un objet",
                                     f"sync:{pair[0]}:{pair[1]}:{int(ctx.t)}", pair, t=ctx.t))
        me = ctx.me
        if me is not None and ctx.me_pos is not None and self._out_of_base(ctx.me_pos, ctx.team) \
                and not bool(getattr(me, "is_dead", False)):
            my_gold = _f(getattr(ctx.game, "current_gold", None), 0.0) or 0.0
            hp = None
            try:
                from treeaicoach.fight import my_hp

                hp = my_hp(ctx.game)
            except Exception:
                hp = None
            if my_gold >= RICH_GOLD or (hp is not None and hp < LOW_HP):
                mine = str(getattr(me, "champion_name", "") or getattr(me, "champion_alias", "") or "moi")
                for k, name, pos, _g in rich:
                    pair = ("_me", k)
                    if k in used or geometry.dist(pos, ctx.me_pos) > SYNC_NEAR \
                            or ctx.t - self._sync_pair.get(pair, -1e9) < SYNC_PAIR_S:
                        continue
                    self._sync_pair[pair] = ctx.t
                    out.append(BrainLine("sync", f"{name}, rentre avec {mine} : assez d'or tous les deux",
                                         f"sync:me:{k}:{int(ctx.t)}", (k,), t=ctx.t))
                    break
        return out

    def _mark(self, line: BrainLine) -> None:
        if line.rule == "objective":
            self._obj_done.add(line.key)


def phrases() -> list[str]:
    """Sample lines (voice coverage / tests): every template with a placeholder champion."""
    out = [f"Ahri, ton adversaire est en base : {x}" for x in ("plaque", "pousse ta lane")]
    out += ["Vi, leur jungler est en base : joue fort", "Jinx, jungler probablement chez toi",
            "Jinx et Lulu, jungler probablement chez vous", "Jinx et Lulu, rentrez ensemble : assez d'or pour un objet",
            "Jinx, rentre avec Garen : assez d'or tous les deux"]
    out += [f"{OBJ_NAME[k]} dans 1:00 : {OBJ_WHO[k]}, rentrez maintenant" for k in OBJ_NAME]
    return out


__all__ = ["TeamBrain", "BrainLine", "earned_estimate", "phrases", "VALIDATION"]
