"""COUPS DE GÉNIE: the macro planner (where to go NOW, and why), 100 % rule based.

:class:`MacroPlanner` looks at the whole map state of one coaching tick (:class:`MacroCtx`: game
phase + structures + death timers from :mod:`treeaicoach.phase`, the tracked minimap icons, the
minion waves of :mod:`treeaicoach.waves`, the enemy jungler Tab intel of
:mod:`treeaicoach.jungle_intel`, the objective timers, gold / level / numbers) and proposes ONE
big macro call at a time, with:

* a short imperative French line (``text``: "Va mid : ta tour du bas est tombée"),
* a one-line WHY (``why``: "Seul en bas tu es une proie ; au milieu tu es près des deux dragons."),
* a minimap target (``target``: arrow "VA ICI" drawn by the overlay) and a banner title,
* a ``genius`` flag: the "COUP DE GÉNIE" badge for the calls that win games (cross-map trades,
  free objectives, post-fight follow-ups, jungler dead windows).

Rules (``kind``), each scored 0..1 from the situation (value of the call x confidence of the
information x team edge - risk), see :func:`evaluate`:

* ``fight_won``  - 2-3 enemies dead after a fight: Baron / dragon / Héraut / inhibitor / tower
  with the respawn window ("Ils sont 3 morts (25 s) : Baron maintenant") (an ace / 4+ dead is
  :class:`treeaicoach.phase.EndGameCaller`'s call);
* ``fight_lost`` - 2+ allies dead more than enemies: "Recule : défends sous ta tour";
* ``jungler_dead`` - enemy jungler dead (respawnTimer): invade (jungler), free objective, or
  plates / aggressive lane (laners);
* ``plates``     - lane opponent dead / back in his base: "Plaque la tour (18 s)" (plate gold
  before 14:00, the tower after);
* ``cross_trade`` / ``free_dragon`` - enemy jungler (or 3+ enemies) committed on the other side
  (seen, Tab farm side, pit crowd): trade the objective / the tower on MY side;
* ``rotate_mid`` - bot outer tower down after laning: bot duo goes mid;
* ``side_wave``  - mid game: a side wave arrives with nobody there and the lane is safe
  (enemy jungler located elsewhere / 3+ enemies seen on the other side): "Change de voie : va top";
* ``split_safe`` - side lane, 3+ enemies seen on the other side: push the tower, back off when
  they disappear;
* ``lane_swap``  - the enemy duo swapped to top: solo laner leaves the 2v1 / duo punishes the solo;
* ``wave_recall`` / ``wave_freeze`` / ``back_off`` - simple wave management and safety
  (cannon wave timing from the game clock).

Policy (:class:`MacroPlanner`): one active call at a time, held at least :data:`HOLD_S` (unless it
becomes invalid: re-checked every tick, cancelled after :data:`GRACE_S` of invalidity), never a
new call during a fight / gank threat / while dead (an active one is cancelled), a gap between
calls and a minimum score that depend on the skill level (:data:`LEVELS`: a beginner gets the
most, an expert only the high-value ones), one-shot situations never repeated.

Riot policy: only the public Live Client data (scores, items, levels, death timers, events),
the game clock and minimap pixels already shown to the player. No enemy cooldown / summoner
spell timer, no automation. Pure Python (+ geometry), thread-safe, never raises.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from treeaicoach import geometry

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------- tunables
HOLD_S = 10.0                  # an active call stays at least this long (unless invalid)
GRACE_S = 2.0                  # invalid this long -> cancelled
DEFAULT_LIFE_S = 20.0
PREEMPT_MARGIN = 0.25          # a new call replaces a held one only if its score is this much higher
FRESH_S = 4.0                  # an enemy "seen" (for counts) at most this long ago
LANING_END_GT = 840.0          # plates fall at 14:00
PLATE_GOLD = 125               # gold per plate (team: 125 to the local player for a solo plate)
RECALL_GOLD = 1100
LOW_HP = 0.35
MINION_FIRST_SPAWN = 30.0      # first wave spawn (game time)
MINION_WAVE_S = 30.0
MINION_WALK_S = 28.0           # spawn -> middle of a lane
OBJ_SOON_S = 45.0              # an objective this close: the group call of positioning.py wins
JG_NEAR_R = 0.35
SIDE_WAVE_EMPTY_R = 0.18
MOVE_SPEED = 360.0             # game units / s (boots, mid game): travel time to a target
#: seconds a group needs to kill / take each target (respawn window = travel + this)
TAKE_S = {"baron": 28.0, "elder": 15.0, "dragon": 14.0, "herald": 16.0, "atakhan": 20.0, "inhib": 8.0,
          "tower": 12.0}
STANCE_SAFE_BLOCK = -4.0       # gauge SAFE (stance score <= this): no "go" call except post-fight ones

ROLE_LANE = {"TOP": "top", "MIDDLE": "mid", "BOTTOM": "bot", "UTILITY": "bot"}
LANE_FR = {"top": "top", "mid": "mid", "bot": "bot"}
SIDE_FR = {"top": "en haut", "mid": "au milieu", "bot": "en bas"}
OBJ_LE = {"baron": "le Baron", "dragon": "le dragon", "elder": "l'ancestral", "herald": "le Héraut",
          "grubs": "les larves", "atakhan": "Atakhan"}
OBJ_TITLE = {"baron": "BARON !", "dragon": "DRAGON !", "elder": "ANCESTRAL !", "herald": "HÉRAUT !",
             "grubs": "LARVES !", "atakhan": "ATAKHAN !"}
DRAGON_UV = (geometry.DRAGON_PIT[0], geometry.DRAGON_PIT[1])
BARON_UV = (geometry.BARON_PIT[0], geometry.BARON_PIT[1])
PIT_UV = {"dragon": DRAGON_UV, "elder": DRAGON_UV, "baron": BARON_UV, "herald": BARON_UV, "grubs": BARON_UV}
OTHER = {"ORDER": "CHAOS", "CHAOS": "ORDER"}
FOUNTAIN = {"ORDER": geometry.BLUE_FOUNTAIN, "CHAOS": geometry.RED_FOUNTAIN}
#: centre of each jungle quadrant (owner team, map half) -> uv (invade target)
JUNGLE_UV = {("ORDER", "top"): (0.24, 0.47), ("ORDER", "bot"): (0.53, 0.76),
             ("CHAOS", "top"): (0.47, 0.24), ("CHAOS", "bot"): (0.76, 0.53)}
#: forward point of MY half of each lane (rotation target)
LANE_POINT = {"ORDER": {"top": (0.085, 0.36), "mid": (0.44, 0.56), "bot": (0.64, 0.915)},
              "CHAOS": {"top": (0.36, 0.085), "mid": (0.56, 0.44), "bot": (0.915, 0.64)}}

#: skill level -> tiers shown, minimum score, gap between two calls (s), max calls / 10 min
LEVELS: dict[str, dict[str, Any]] = {
    "debutant": {"tiers": frozenset({"basic", "mid", "high"}), "min_score": 0.0, "gap": 30.0},
    "intermediaire": {"tiers": frozenset({"basic", "mid", "high"}), "min_score": 0.30, "gap": 45.0},
    "avance": {"tiers": frozenset({"mid", "high"}), "min_score": 0.45, "gap": 60.0},
    "expert": {"tiers": frozenset({"high"}), "min_score": 0.60, "gap": 90.0},
}
#: per-kind cooldown (s) after a call of this kind (shown or cancelled)
KIND_COOLDOWN_S: dict[str, float] = {
    "fight_won": 40.0, "fight_lost": 40.0, "jungler_dead": 60.0, "plates": 60.0, "cross_trade": 180.0,
    "free_dragon": 180.0, "rotate_mid": 120.0, "side_wave": 75.0, "split_safe": 90.0, "lane_swap": 120.0,
    "wave_recall": 120.0, "wave_freeze": 120.0, "back_off": 75.0,
}
#: short windows: they skip the global gap (they still respect the hold of the active call)
URGENT_KINDS = frozenset({"fight_won", "fight_lost", "jungler_dead", "plates"})
#: "go" calls that stay valid whatever my own gauge says (the numbers decide, not my lane)
POST_FIGHT_KINDS = frozenset({"fight_won"})
#: coach.MapCoach rules saying the same thing in words (suppressed around a call of the kind)
OVERLAPS: dict[str, frozenset[str]] = {
    "plates": frozenset({"lane_dead", "lane_recall", "lane_left"}),
    "jungler_dead": frozenset({"jungler_dead"}),
    "fight_won": frozenset({"baron_pick", "objective_window"}),
    "fight_lost": frozenset(),
    "cross_trade": frozenset({"objective_trade", "jungler_side", "pressure"}),
    "free_dragon": frozenset({"objective_trade", "jungler_side", "pressure"}),
    "split_safe": frozenset({"pressure"}),
    "side_wave": frozenset({"wave_big"}),
    "wave_recall": frozenset({"recall_item", "wave_push"}),
    "wave_freeze": frozenset({"wave_push", "freeze", "jungler_side"}),
    "back_off": frozenset({"lane_left", "missing"}),
    "rotate_mid": frozenset(),
    "lane_swap": frozenset({"bot_missing"}),
}
OVERLAP_S = 90.0
BADGE_MIN_SCORE = 0.6          # the "COUP DE GÉNIE" badge only for strong calls...
BADGE_KIND_GAP_S = 240.0       # ...and not twice in 4 min for the same kind


def _f(x: Any, default: float | None = None) -> float | None:
    if x is None or isinstance(x, bool):
        return default
    try:
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        return default
    return v if math.isfinite(v) else default


def _uv(p: Any) -> tuple[float, float] | None:
    try:
        if p is None:
            return None
        u, v = float(p[0]), float(p[1])
        return (u, v) if math.isfinite(u) and math.isfinite(v) else None
    except (TypeError, ValueError, IndexError):
        return None


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _secs(x: float) -> int:
    return int(max(0, round(x)))


def side_of(uv: tuple[float, float]) -> str:
    """"top" / "mid" / "bot" half of the map (mid = close to the mid lane diagonal)."""
    if abs(uv[0] + uv[1] - 1.0) < 0.12:
        return "mid"
    return geometry.side_of(*uv)


def lane_uv(lane: str, s_from_my_base: float, team: str | None) -> tuple[float, float]:
    """Point at the fraction ``s`` (0 = my base, 1 = theirs) of ``lane``'s centre line."""
    poly = {"top": geometry.TOP_LANE_POLYLINE, "mid": geometry.MID_LANE_POLYLINE,
            "bot": tuple(reversed(geometry.BOT_LANE_POLYLINE))}.get(lane, geometry.MID_LANE_POLYLINE)
    s = _clamp(float(s_from_my_base))
    if geometry.normalize_team(team) == "CHAOS":
        s = 1.0 - s
    segs = [(a, b, math.hypot(b[0] - a[0], b[1] - a[1])) for a, b in zip(poly, poly[1:])]
    total = sum(L for _a, _b, L in segs) or 1.0
    want, acc = s * total, 0.0
    for a, b, L in segs:
        if acc + L >= want and L > 0:
            k = (want - acc) / L
            return (round(a[0] + k * (b[0] - a[0]), 4), round(a[1] + k * (b[1] - a[1]), 4))
        acc += L
    return poly[-1]


def wave_number(gt: float) -> int:
    """Index (1-based) of the last minion wave spawned at game time ``gt`` (0 before the first)."""
    if gt < MINION_FIRST_SPAWN:
        return 0
    return int((gt - MINION_FIRST_SPAWN) // MINION_WAVE_S) + 1


def is_cannon_wave(k: int, spawn_gt: float) -> bool:
    """Cannon (siege) minion in wave ``k``: every 3rd wave, every 2nd from 15:00, all from 25:00."""
    if k <= 0:
        return False
    if spawn_gt >= 1500.0:
        return True
    if spawn_gt >= 900.0:
        return k % 2 == 0
    return k % 3 == 0


def next_cannon_arrival(gt: float) -> float:
    """Seconds until the next cannon wave reaches the middle of a lane (>= 0)."""
    k = max(1, wave_number(gt - MINION_WALK_S))
    for kk in range(k, k + 8):
        spawn = MINION_FIRST_SPAWN + (kk - 1) * MINION_WAVE_S
        arrive = spawn + MINION_WALK_S
        if arrive >= gt and is_cannon_wave(kk, spawn):
            return arrive - gt
    return 90.0


# ----------------------------------------------------------------------------- data
@dataclass(frozen=True)
class GeniusCall:
    """One macro call (see the module docstring)."""

    kind: str
    ident: str                     # situation id (one-shot memory / re-validation)
    title: str                     # banner title ("VA MID", "BARON !")
    text: str                      # HUD line, imperative + short reason
    why: str                       # the one-line WHY (banner subtitle / badge)
    target: tuple[float, float] | None = None
    label: str = "VA ICI"          # minimap tag of the target
    color: str = "gold"            # "gold" | "safe" | "danger" | "teal"
    tier: str = "mid"              # "basic" | "mid" | "high"
    score: float = 0.5             # 0..1
    priority: int = 50
    genius: bool = False           # "COUP DE GÉNIE" badge
    life_s: float = DEFAULT_LIFE_S
    factors: tuple[str, ...] = ()  # scoring breakdown (debug / tests / AI plan)
    t: float = 0.0


@dataclass
class MacroUpdate:
    new: GeniusCall | None = None
    cancelled: GeniusCall | None = None
    cancel_reason: str = ""
    active: GeniusCall | None = None


@dataclass
class JunglerLoc:
    """Where the enemy jungler probably is."""

    known: bool = False
    dead: bool = False
    respawn: float = 0.0
    side: str | None = None        # "top" | "bot" | "mid" (map half)
    uv: tuple[float, float] | None = None
    conf: float = 0.0              # 0..1
    age: float = math.inf          # seconds since the information
    source: str = ""               # "seen" | "tab" | "pit" | ""
    name: str = ""


@dataclass
class MacroCtx:
    """Everything one planner tick looks at (built by :func:`build_ctx`)."""

    t: float = 0.0
    gt: float = 0.0
    phase: str = "laning"
    my_team: str | None = None
    role: str | None = None
    me_uv: tuple[float, float] | None = None
    me_alive: bool = True
    in_base: bool = False
    hp: float | None = None
    gold: float = 0.0
    my_level: int = 0
    allies: list = field(default_factory=list)       # fight.Seen (visible allies)
    enemies: list = field(default_factory=list)      # fight.Seen (every enemy track)
    st: Any = None                                   # phase.MapState
    objectives: dict = field(default_factory=dict)   # key -> (alive, remaining s | None)
    waves: dict = field(default_factory=dict)        # lane -> LaneWave / dict
    jint: Any = None                                 # jungle_intel.JungleIntel
    game: Any = None
    enemy_roles: dict = field(default_factory=dict)  # alias (lower) -> role
    enemy_names: dict = field(default_factory=dict)  # alias (lower) -> display name
    lane_opps: tuple = ()                            # aliases (lower) of my lane opponents
    jungler_alias: str | None = None                 # lower case
    gold_diff: float = 0.0                           # team gold diff (Tab)
    in_fight: bool = False
    threat: int = 0
    recent_director_call: bool = False               # phase.EndGameCaller spoke in the last seconds
    stance_score: float | None = None                # play gauge score (coach.Stance.score), None = unknown
    keep: bool = False                               # re-validating an active call: relaxed thresholds

    @property
    def enemy_team(self) -> str | None:
        return OTHER.get(self.my_team or "")


# ----------------------------------------------------------------------------- context
def build_ctx(t: float, gt: float, game: Any, st: Any, *, role: str | None, me_uv: Any, allies: Iterable[Any] = (),
              enemies: Iterable[Any] = (), objectives: Iterable[Any] = (), waves: Any = None, jint: Any = None,
              roles: Any = None, scoreboard: Any = None, in_fight: bool = False, threat: int = 0,
              in_base: bool = False, recent_director_call: bool = False,
              stance_score: float | None = None) -> MacroCtx:
    """A :class:`MacroCtx` from the engine's objects (any of them may be None). Never raises."""
    ctx = MacroCtx(t=float(t), gt=float(gt))
    try:
        me = getattr(game, "me", None)
        ctx.game = game
        ctx.st = st
        ctx.phase = str(getattr(st, "phase", "laning") or "laning")
        ctx.my_team = geometry.normalize_team(getattr(me, "team", None)) if me is not None else None
        ctx.role = str(role or getattr(me, "position", "") or "").upper() or None
        ctx.me_uv = _uv(me_uv)
        ctx.me_alive = me is not None and not bool(getattr(me, "is_dead", False))
        ctx.in_base = bool(in_base)
        try:
            from treeaicoach.fight import my_hp

            ctx.hp = my_hp(game)
        except Exception:
            ctx.hp = None
        ctx.gold = _f(getattr(game, "current_gold", None), 0.0) or 0.0
        ctx.my_level = int(_f(getattr(me, "level", None), 0) or 0)
        ctx.allies = list(allies or [])
        ctx.enemies = list(enemies or [])
        objs: dict[str, tuple[bool, float | None]] = {}
        for o in objectives or []:
            key = str(getattr(o, "key", "") or "")
            if key:
                objs[key] = (bool(getattr(o, "alive", False)), _f(getattr(o, "remaining", None)))
        ctx.objectives = objs
        ctx.waves = dict(waves or {})
        ctx.jint = jint
        for p in getattr(game, "enemies", None) or []:
            a = str(getattr(p, "champion_alias", "") or "").lower()
            if not a:
                continue
            r = None
            try:
                r = roles.role_of(a, "enemy") if roles is not None and hasattr(roles, "role_of") else None
            except Exception:
                r = None
            ctx.enemy_roles[a] = str(r or getattr(p, "position", "") or "").upper()
            ctx.enemy_names[a] = str(getattr(p, "champion_name", "") or getattr(p, "champion_alias", ""))
        lane_opps: tuple = ()
        try:
            if roles is not None and hasattr(roles, "lane_opponents"):
                lane_opps = tuple(str(a).lower() for a in roles.lane_opponents() or ())
        except Exception:
            lane_opps = ()
        if not lane_opps and ctx.role in ROLE_LANE:
            want = ("BOTTOM", "UTILITY") if ctx.role in ("BOTTOM", "UTILITY") else (ctx.role,)
            lane_opps = tuple(a for a, r in ctx.enemy_roles.items() if r in want)
        ctx.lane_opps = lane_opps
        jg = None
        try:
            jg = roles.enemy_jungler() if roles is not None and hasattr(roles, "enemy_jungler") else None
        except Exception:
            jg = None
        if not jg and hasattr(game, "enemy_jungler"):
            p = game.enemy_jungler()
            jg = getattr(p, "champion_alias", None) if p is not None else None
        ctx.jungler_alias = str(jg).lower() if jg else None
        ctx.gold_diff = _f(getattr(scoreboard, "team_gold_diff", None), 0.0) or 0.0
        ctx.in_fight = bool(in_fight)
        ctx.threat = int(_f(threat, 0) or 0)
        ctx.recent_director_call = bool(recent_director_call)
        ctx.stance_score = _f(stance_score)
    except Exception:
        log.debug("macro.build_ctx failed", exc_info=True)
    return ctx


# ----------------------------------------------------------------------------- situation helpers
def _player(ctx: MacroCtx, alias: str | None, side: str = "enemies") -> Any:
    a = str(alias or "").lower()
    for p in getattr(ctx.game, side, None) or []:
        if str(getattr(p, "champion_alias", "") or "").lower() == a:
            return p
    return None


def _respawn(p: Any) -> float:
    if p is None or not bool(getattr(p, "is_dead", False)):
        return 0.0
    return max(0.0, _f(getattr(p, "respawn_timer", None), 0.0) or 0.0)


def _dead_list(ctx: MacroCtx, side: str = "enemies", min_s: float = 0.0) -> list[tuple[str, str, float]]:
    """``(alias lower, name, respawn s)`` of the dead players of ``side`` ("enemies" / "allies")."""
    out = []
    for p in getattr(ctx.game, side, None) or []:
        r = _respawn(p)
        if bool(getattr(p, "is_dead", False)) and r >= min_s:
            out.append((str(getattr(p, "champion_alias", "") or "").lower(),
                        str(getattr(p, "champion_name", "") or getattr(p, "champion_alias", "")), r))
    return out


def _track(ctx: MacroCtx, alias: str | None) -> Any:
    a = str(alias or "").lower()
    for e in ctx.enemies:
        if str(getattr(e, "alias", "") or "").lower() == a:
            return e
    return None


def _fresh_enemies(ctx: MacroCtx, max_hidden: float = FRESH_S) -> list[Any]:
    return [e for e in ctx.enemies if _uv(getattr(e, "uv", None)) is not None
            and (bool(getattr(e, "visible", False)) or (_f(getattr(e, "hidden_s", None), 99.0) or 0.0) <= max_hidden)]


def _obj_up(ctx: MacroCtx, key: str, within: float = 0.0) -> bool:
    o = ctx.objectives.get(key)
    if o is None:
        return False
    alive, rem = o
    return alive or (rem is not None and 0.0 <= rem <= within)


def _obj_soon(ctx: MacroCtx, within: float = OBJ_SOON_S) -> str | None:
    """An epic objective spawning within ``within`` s (not yet alive), else None."""
    for key, (alive, rem) in ctx.objectives.items():
        if not alive and rem is not None and 0.0 < rem <= within and key in PIT_UV:
            return key
    return None


def jungler_location(ctx: MacroCtx) -> JunglerLoc:
    """Best guess of the enemy jungler's whereabouts: seen on the minimap (confidence decays
    over 40 s), Tab intel (farming side, decays over 30 s), crowd at a pit. Never raises."""
    loc = JunglerLoc()
    try:
        a = ctx.jungler_alias
        if not a:
            return loc
        loc.known = True
        p = _player(ctx, a)
        loc.name = str(getattr(p, "champion_name", "") or a) if p is not None else a
        if p is not None and bool(getattr(p, "is_dead", False)):
            loc.dead, loc.respawn, loc.conf, loc.age, loc.source = True, _respawn(p), 1.0, 0.0, "tab"
            return loc
        tr = _track(ctx, a)
        uv = _uv(getattr(tr, "uv", None)) if tr is not None else None
        if tr is not None and uv is not None:
            hid = 0.0 if bool(getattr(tr, "visible", False)) else (_f(getattr(tr, "hidden_s", None), 99.0) or 99.0)
            conf = _clamp(1.0 - hid / 40.0)
            if conf > 0.0:
                loc.side, loc.uv, loc.conf, loc.age, loc.source = side_of(uv), uv, conf, hid, "seen"
        ji = ctx.jint
        if ji is not None and str(getattr(ji, "alias", "") or "").lower() in ("", a):
            fside = getattr(ji, "farm_side", None)
            ft = _f(getattr(ji, "last_farm_t", None))
            if fside in ("top", "bot") and ft is not None:
                age = max(0.0, ctx.t - ft)
                conf = _clamp(0.8 * (1.0 - age / 30.0))
                if conf > loc.conf:
                    pts = getattr(ji, "farm_points", ()) or ()
                    cu = sum(p_[0] for p_ in pts) / len(pts) if pts else None
                    cv = sum(p_[1] for p_ in pts) / len(pts) if pts else None
                    loc.side, loc.conf, loc.age, loc.source = fside, conf, age, "tab"
                    loc.uv = (cu, cv) if cu is not None else None
    except Exception:
        log.debug("jungler_location failed", exc_info=True)
    return loc


def pit_crowd(ctx: MacroCtx, pit: tuple[float, float], r: float = 0.22) -> int:
    """Enemies seen (fresh) around a pit."""
    return sum(1 for e in _fresh_enemies(ctx) if geometry.dist(_uv(e.uv), pit) < r)  # type: ignore[arg-type]


def team_edge(ctx: MacroCtx) -> tuple[float, list[str]]:
    """-1..1 advantage of my team (gold diff, numbers alive), with the reasons."""
    why: list[str] = []
    e = _clamp(ctx.gold_diff / 6000.0, -0.5, 0.5)
    if abs(ctx.gold_diff) >= 1500:
        why.append(f"or {'+' if ctx.gold_diff > 0 else ''}{int(ctx.gold_diff)}")
    n_en = len(_dead_list(ctx, "enemies", 5.0))
    n_al = len(_dead_list(ctx, "allies", 5.0)) + (0 if ctx.me_alive else 1)
    if n_en != n_al:
        e += 0.15 * (n_en - n_al)
        why.append(f"{5 - n_al} contre {5 - n_en}")
    lv_al = [int(_f(getattr(p, "level", 0), 0) or 0) for p in (getattr(ctx.game, "allies", None) or [])]
    lv_al.append(ctx.my_level)
    lv_en = [int(_f(getattr(p, "level", 0), 0) or 0) for p in (getattr(ctx.game, "enemies", None) or [])]
    if lv_al and lv_en and ctx.my_level:
        dl = sum(lv_al) / len(lv_al) - sum(lv_en) / len(lv_en)
        e += 0.05 * max(-3.0, min(3.0, dl))
        if abs(dl) >= 1.0:
            why.append(f"niveaux {'+' if dl > 0 else ''}{dl:.1f}")
    return _clamp(e, -1.0, 1.0), why


def vision_share(ctx: MacroCtx, window_s: float = 8.0) -> float:
    """Share (0..1) of the living enemies seen on the minimap in the last ``window_s`` seconds:
    how much the map is known (a split / a rotation is safer when it is high)."""
    alive = [str(getattr(p, "champion_alias", "") or "").lower() for p in (getattr(ctx.game, "enemies", None) or [])
             if not bool(getattr(p, "is_dead", False))]
    if not alive:
        return 0.0
    seen = {str(getattr(e, "alias", "") or "").lower() for e in _fresh_enemies(ctx, window_s)}
    return sum(1 for a in alive if a in seen) / len(alive)


def _lane_wave(ctx: MacroCtx, lane: str | None) -> Any:
    return ctx.waves.get(lane) if lane else None


def _my_lane(ctx: MacroCtx) -> str | None:
    return ROLE_LANE.get(ctx.role or "")


def _zone_lane(uv: tuple[float, float] | None) -> str | None:
    if uv is None:
        return None
    return geometry.lane_of(geometry.classify_zone(*uv))


def _standing(ctx: MacroCtx, team: str | None, lane: str) -> list[tuple[int, tuple[float, float]]]:
    """``(tier, uv)`` of the standing turrets of ``team`` in ``lane``, outer first."""
    st = ctx.st
    if st is None or team is None:
        return []
    try:
        return sorted((tier, uv) for ln, tier, uv in st.standing_turrets(team) if ln == lane and tier <= 3)
    except Exception:
        return []


def _forward(ctx: MacroCtx) -> bool:
    """Am I past the middle of the map towards the enemy base?"""
    if ctx.me_uv is None or ctx.my_team not in FOUNTAIN:
        return False
    mine = geometry.dist(ctx.me_uv, FOUNTAIN[ctx.my_team])
    theirs = geometry.dist(ctx.me_uv, FOUNTAIN[OTHER[ctx.my_team]])
    return mine > theirs + 0.05


def _safe_uv(ctx: MacroCtx) -> tuple[float, float] | None:
    st = ctx.st
    try:
        return st.nearest_safe_uv(ctx.me_uv) if st is not None else None
    except Exception:
        return None


def travel_s(ctx: MacroCtx, target: Any) -> float:
    """Seconds to walk from me to ``target`` (0 when either is unknown)."""
    tgt = _uv(target)
    if tgt is None or ctx.me_uv is None:
        return 0.0
    return geometry.dist(ctx.me_uv, tgt) * geometry.MAP_GAME_UNITS / MOVE_SPEED


#: minimum respawn window per target, whatever the distances (a Baron started with 20 s is a throw)
WINDOW_FLOOR_S = {"baron": 25.0, "elder": 18.0, "dragon": 15.0, "herald": 15.0, "atakhan": 18.0, "tower": 12.0,
                  "inhib": 10.0}


def _window_ok(ctx: MacroCtx, window: float, target: Any, what: str) -> bool:
    """Is a respawn ``window`` long enough for this target? The dead enemies come back from their
    fountain: they can contest at ``window + their walk``; we need ``our walk + the take time``.
    Also a floor per target (:data:`WINDOW_FLOOR_S`). Re-validating an active call: always."""
    if ctx.keep:
        return True
    if window < WINDOW_FLOOR_S.get(what, 12.0):
        return False
    take = TAKE_S.get(what, 12.0)
    if what == "baron" and ctx.gt >= 1500.0:
        take = 20.0
    tgt = _uv(target)
    back = 0.0
    if tgt is not None and ctx.enemy_team in FOUNTAIN:
        back = geometry.dist(FOUNTAIN[ctx.enemy_team], tgt) * geometry.MAP_GAME_UNITS / (MOVE_SPEED * 1.15)
    return window + back >= travel_s(ctx, target) + take


def _call(kind: str, ident: str, title: str, text: str, why: str, target: Any, *, tier: str, score: float,
          priority: int, color: str = "gold", genius: bool = False, life: float = DEFAULT_LIFE_S,
          label: str = "VA ICI", factors: Iterable[str] = ()) -> GeniusCall:
    return GeniusCall(kind, ident, title[:22], text, why, _uv(target), label[:16], color, tier,
                      round(_clamp(score), 3), int(priority), bool(genius), float(life), tuple(factors))


# ----------------------------------------------------------------------------- rules
def _rule_fight_won(ctx: MacroCtx) -> GeniusCall | None:
    if not ctx.me_alive or ctx.recent_director_call:
        return None
    dead = _dead_list(ctx, "enemies", 2.0 if ctx.keep else 8.0)
    n = len(dead)
    n_al = len(_dead_list(ctx, "allies", 3.0))
    if not (2 <= n <= (5 if ctx.keep else 3)) or n - n_al < (1 if ctx.keep else 2):
        return None
    window = min(r for _a, _n, r in dead)
    if ctx.keep:
        window = max(window, 30.0)               # still valid while the numbers hold
    jg_dead = ctx.jungler_alias is not None and any(a == ctx.jungler_alias for a, _n, _r in dead)
    edge, why_edge = team_edge(ctx)
    alive_vs = f"{5 - n_al} contre {5 - n}"
    opts: list[tuple[float, str, str, Any, str]] = []      # (value, what, title, target, short)
    # Baron: 3 dead, or 2 dead with their jungler (no smite to steal it), 4+ of us alive, and the
    # respawn window covers the walk + the kill (a Baron started with 20 s of window is a throw)
    if _obj_up(ctx, "baron") and ctx.gt >= 1200 and n_al <= 1 and (n >= 3 or jg_dead) \
            and _window_ok(ctx, window, BARON_UV, "baron"):
        opts.append((1.0, "le Baron", "BARON !", BARON_UV, "le Baron"))
    if _obj_up(ctx, "elder") and n_al <= 1 and _window_ok(ctx, window, DRAGON_UV, "elder"):
        opts.append((1.0, "l'ancestral", "ANCESTRAL !", DRAGON_UV, "l'ancestral"))
    if _obj_up(ctx, "dragon") and _window_ok(ctx, window, DRAGON_UV, "dragon"):
        opts.append((0.8 + (0.1 if jg_dead else 0.0), "le dragon", "DRAGON !", DRAGON_UV, "le dragon"))
    if _obj_up(ctx, "herald") and _window_ok(ctx, window, BARON_UV, "herald"):
        opts.append((0.65, OBJ_LE["herald"], OBJ_TITLE["herald"], BARON_UV, OBJ_LE["herald"]))
    theirs = ctx.enemy_team
    st = ctx.st
    if theirs is not None and st is not None:
        for lane in ("mid", "bot", "top"):
            stand = _standing(ctx, theirs, lane)
            inhib_down = (theirs, lane) in (getattr(st, "inhibs_down", ()) or ())
            if stand and stand[0][0] == 3 and _window_ok(ctx, window, stand[0][1], "tower"):
                opts.append((0.85, "la tour de l'inhibiteur", "INHIBITEUR !", stand[0][1],
                             f"la tour de l'inhibiteur {LANE_FR[lane]}"))
            elif not stand and not inhib_down and window >= 12:
                try:
                    from treeaicoach.phase import TURRET_UV
                    inhib_uv = TURRET_UV.get((theirs, lane, 3))
                except Exception:
                    inhib_uv = None
                opts.append((0.85, "l'inhibiteur", "INHIBITEUR !", inhib_uv, f"l'inhibiteur {LANE_FR[lane]}"))
        best_t = None
        ref = ctx.me_uv or (0.5, 0.5)
        for lane in ("top", "mid", "bot"):
            stand = _standing(ctx, theirs, lane)
            if stand and stand[0][0] <= 2:
                d = geometry.dist(ref, stand[0][1])
                if best_t is None or d < best_t[0]:
                    best_t = (d, lane, stand[0][1])
        if best_t is not None and _window_ok(ctx, window, best_t[2], "tower"):
            opts.append((0.6 - 0.2 * min(1.0, best_t[0]), "la tour", "TOUR !", best_t[2], f"la tour {LANE_FR[best_t[1]]}"))
    opts = [o for o in opts if o[3] is not None]
    if not opts:
        return None
    value, what, title, target, short = max(opts, key=lambda o: o[0])
    score = value * _clamp(0.55 + window / 60.0) * _clamp(0.75 + 0.25 * edge + 0.1 * (n - 2))
    return _call("fight_won", f"fight_won:{int(ctx.gt // 30)}:{what}", title,
                 f"Ils sont {n} morts ({_secs(window)} s) : {what} maintenant !".replace(": le Baron", ": Baron"),
                 f"À {alive_vs}, ils ne peuvent pas défendre : {_secs(window)} s suffisent pour {short}.",
                 target, tier="high", score=score, priority=94, color="safe", genius=True,
                 life=max(8.0, min(window, 30.0)), label=title.rstrip(" !")[:16],
                 factors=(f"{n} morts", f"fenêtre {_secs(window)} s", *why_edge))


def _rule_fight_lost(ctx: MacroCtx) -> GeniusCall | None:
    if not ctx.me_alive or ctx.in_base:
        return None
    al = _dead_list(ctx, "allies", 2.0 if ctx.keep else 8.0)
    en = _dead_list(ctx, "enemies", 3.0)
    if len(al) < 2 or len(al) - len(en) < (1 if ctx.keep else 2):
        return None
    window = sorted(r for _a, _n, r in al)[len(al) - 2]          # until we are only 1 down
    safe = _safe_uv(ctx)
    if safe is None:
        return None
    score = _clamp(0.5 + 0.15 * (len(al) - len(en)) + (0.15 if _forward(ctx) else 0.0))
    return _call("fight_lost", f"fight_lost:{int(ctx.gt // 30)}", "RECULE",
                 "Recule : défends sous ta tour, ne contre-attaque pas.",
                 f"Vous êtes {5 - len(al)} contre {5 - len(en)} pendant {_secs(window)} s : attends que tes "
                 "alliés reviennent.",
                 safe, tier="mid", score=score, priority=96, color="danger", life=max(8.0, min(window, 25.0)),
                 label="REPLI", factors=(f"{len(al)} alliés morts", f"{len(en)} ennemis morts"))


def _rule_jungler_dead(ctx: MacroCtx) -> GeniusCall | None:
    if not ctx.me_alive:
        return None
    jl = jungler_location(ctx)
    if not jl.dead or jl.respawn < (2.0 if ctx.keep else 15.0):
        return None
    w = _secs(jl.respawn)
    name = jl.name or "Leur jungler"
    theirs = ctx.enemy_team
    role = ctx.role
    ident = f"jungler_dead:{int((ctx.gt + jl.respawn) // 10)}"
    # a free epic objective first (dragon side for bot / mid / jungle, top side for top / mid / jungle)
    for key, roles_ok in (("elder", None), ("baron", None), ("dragon", ("JUNGLE", "BOTTOM", "UTILITY", "MIDDLE")),
                          ("herald", ("JUNGLE", "TOP", "MIDDLE")), ("grubs", ("JUNGLE", "TOP", "MIDDLE"))):
        # Baron / Elder: their jungler alone dead is not enough (4 against 5 on a 50-50 objective)
        if key in ("baron", "elder") and (len(_dead_list(ctx, "enemies", 15.0)) < 2
                                          or (key == "baron" and ctx.gt < 1200)):
            continue
        if not _obj_up(ctx, key, within=5.0) or (roles_ok is not None and role not in roles_ok):
            continue
        # a laner without his jungler takes a dragon far too slowly (and no smite on our side)
        if role != "JUNGLE" and not _ally_jungler_alive(ctx):
            continue
        if not _window_ok(ctx, jl.respawn, PIT_UV.get(key), key):
            continue
        what = OBJ_LE[key]
        return _call("jungler_dead", ident, OBJ_TITLE[key], f"{name} est mort ({w} s) : prenez {what} !",
                     f"Sans jungler, ils ne peuvent pas voler {what} au châtiment : {w} s de fenêtre.",
                     PIT_UV.get(key), tier="high", score=_clamp(0.7 + jl.respawn / 100.0), priority=90,
                     color="safe", genius=True, life=max(10.0, min(jl.respawn, 30.0)),
                     label=OBJ_TITLE[key].rstrip(" !"), factors=(f"jungler mort {w} s", what))
    if role == "JUNGLE" and theirs is not None:
        ref = ctx.me_uv or (0.5, 0.5)
        half = min(("top", "bot"), key=lambda h: geometry.dist(ref, JUNGLE_UV[(theirs, h)]))
        return _call("jungler_dead", ident, "ENVAHIS !", f"{name} est mort ({w} s) : envahis et prends ses camps.",
                     f"Personne ne défend sa jungle pendant {w} s : chaque camp volé le met en retard.",
                     JUNGLE_UV[(theirs, half)], tier="high", score=_clamp(0.6 + jl.respawn / 100.0), priority=88,
                     color="safe", genius=True, life=max(10.0, min(jl.respawn, 30.0)), label="ENVAHIS",
                     factors=(f"jungler mort {w} s",))
    lane = _my_lane(ctx)
    if lane and ctx.phase == "laning" and _zone_lane(ctx.me_uv) == lane and role != "UTILITY" \
            and (ctx.hp is None or ctx.hp >= 0.5):
        target = lane_uv(lane, 0.6, ctx.my_team)
        return _call("jungler_dead", ident, "JOUE AVANCÉ",
                     f"{name} est mort ({w} s) : pousse ta vague, aucun gank possible.",
                     "Leur jungler ne peut pas venir : prends l'avantage dans ta voie.",
                     target, tier="basic", score=0.45, priority=80, color="safe",
                     life=max(10.0, min(jl.respawn, 25.0)), label="POUSSE", factors=(f"jungler mort {w} s",))
    return None


def _rule_plates(ctx: MacroCtx) -> GeniusCall | None:
    lane = _my_lane(ctx)
    if not ctx.me_alive or lane is None or ctx.role == "JUNGLE" or ctx.in_base:
        return None
    if _zone_lane(ctx.me_uv) != lane or (ctx.hp is not None and ctx.hp < LOW_HP):
        return None
    if not ctx.lane_opps:
        return None
    theirs = ctx.enemy_team
    stand = _standing(ctx, theirs, lane)
    if not stand:
        return None
    absent: list[tuple[str, float, str]] = []          # (name, window, why)
    for a in ctx.lane_opps:
        p = _player(ctx, a)
        if p is None:
            continue
        r = _respawn(p)
        nm = ctx.enemy_names.get(a, a)
        if r >= (1.0 if ctx.keep else 8.0):
            absent.append((nm, r, "mort"))
            continue
        tr = _track(ctx, a)
        uv = _uv(getattr(tr, "uv", None)) if tr is not None else None
        if uv is not None and theirs is not None and geometry.in_fountain(uv[0], uv[1], theirs) \
                and (_f(getattr(tr, "hidden_s", None), 99.0) or 0.0) <= 6.0:
            absent.append((nm, 20.0, "base"))
    present = len(ctx.lane_opps) - len(absent)
    if not absent or present > 0:                         # bot lane: one of two away is not enough
        return None
    jl = jungler_location(ctx)
    tower = stand[0][1]
    if jl.uv is not None and not jl.dead and jl.conf >= 0.5 and geometry.dist(jl.uv, tower) < JG_NEAR_R:
        return None                                       # their jungler is right there
    jg_unknown = jl.known and not jl.dead and jl.conf < 0.3
    lw = _lane_wave(ctx, lane)
    meet = _f(_get(lw, "meet")) if lw is not None else None
    if jg_unknown and not ctx.keep and meet is not None and meet < 0.55:
        return None                                       # alone at their tower, jungler unseen, no wave: bait
    window = min(w for _n, w, _y in absent)
    who = " et ".join(n for n, _w, _y in absent[:2])
    dead = all(y == "mort" for _n, _w, y in absent)
    plates = ctx.gt < LANING_END_GT and stand[0][0] == 1
    verb = ("sont morts" if len(absent) > 1 else "est mort") if dead else ("sont en base" if len(absent) > 1 else "est en base")
    if plates:
        text = f"Plaque la tour ({_secs(window)} s) : {who} {verb}." if dead else f"Pousse et plaque la tour : {who} {verb}."
        why = f"Chaque plaque = {PLATE_GOLD} PO jusqu'à 14:00, et personne ne la défend."
    else:
        text = f"Frappe la tour ({_secs(window)} s) : {who} {verb}." if dead else f"Pousse et frappe la tour : {who} {verb}."
        why = "Une tour = de l'or pour toute l'équipe et la carte s'ouvre pour vous."
    if jg_unknown:
        why = why + f" Leur jungler n'est pas visible : reste sur ta vague."
    risk = 0.0 if jl.dead else (0.2 if jl.conf < 0.3 else 0.0)
    score = _clamp(0.45 + window / 80.0 + (0.1 if plates else 0.0) - risk)
    return _call("plates", f"plates:{'-'.join(sorted(a for a in ctx.lane_opps))}:{int(ctx.gt // 20)}",
                 "PLAQUES !" if plates else "TOUR !", text, why, tower, tier="mid", score=score, priority=82,
                 color="safe", life=max(8.0, min(window, 25.0)), label="TOUR",
                 factors=(f"adversaire absent {_secs(window)} s", "plaques" if plates else "tour"))


def _rule_cross_map(ctx: MacroCtx) -> GeniusCall | None:
    """Enemy jungler (or 3+ enemies) committed on one side -> trade on the other side."""
    if not ctx.me_alive or ctx.gt < 180.0 or ctx.my_team is None:
        return None
    jl = jungler_location(ctx)
    if jl.dead:
        return None
    crowd_bot = pit_crowd(ctx, DRAGON_UV)
    crowd_top = pit_crowd(ctx, BARON_UV)
    need = 0.3 if ctx.keep else 0.55
    bot_commit = (jl.side == "bot" and jl.conf >= need) or crowd_bot >= 3
    top_commit = (jl.side == "top" and jl.conf >= need) or crowd_top >= 3
    if bot_commit == top_commit:
        return None
    theirs = ctx.enemy_team
    role = ctx.role or ""
    who = (f"{crowd_bot if bot_commit else crowd_top} ennemis sont"
           if max(crowd_bot, crowd_top) >= 3 else f"{jl.name or 'leur jungler'} est")
    drag_up = _obj_up(ctx, "dragon", 30.0) or _obj_up(ctx, "elder", 30.0)
    near_drag = crowd_bot >= 2 or bool(jl.uv and geometry.dist(jl.uv, DRAGON_UV) < 0.2)
    where = "au dragon" if bot_commit and drag_up and near_drag \
        else "en bas" if bot_commit else "au Baron" if crowd_top >= 2 else "en haut"
    conf = max(jl.conf if jl.side in ("top", "bot") else 0.0, 0.85 if max(crowd_bot, crowd_top) >= 3 else 0.0)
    edge, why_edge = team_edge(ctx)
    if bot_commit:
        if role not in ("TOP", "JUNGLE", "MIDDLE"):
            return None
        for key in ("herald", "grubs"):
            if _obj_up(ctx, key) and role in ("JUNGLE", "TOP", "MIDDLE"):
                if role != "JUNGLE" and not _ally_jungler_alive(ctx):
                    break
                verb = "Prends" if role == "JUNGLE" else "Aide ton jungler à prendre"
                return _call("cross_trade", f"cross:{key}:{int(ctx.gt // 60)}", OBJ_TITLE[key],
                             f"{verb} {OBJ_LE[key]} maintenant : {who} {where}.",
                             f"Ils sont de l'autre côté : échange l'objectif au lieu de perdre un combat.",
                             BARON_UV, tier="high", score=_clamp(0.55 + 0.35 * conf + 0.1 * edge), priority=86,
                             color="safe", genius=True, life=18.0, label=OBJ_TITLE[key].rstrip(" !"),
                             factors=(f"jungler {jl.source} {jl.side} ({conf:.0%})", *why_edge))
        stand = _standing(ctx, theirs, "top")
        if role == "TOP" and stand and _zone_lane(ctx.me_uv) == "top" and _tower_free(ctx, "top"):
            return _call("cross_trade", f"cross:tower:{int(ctx.gt // 60)}", "TOUR !",
                         f"Frappe leur tour du haut maintenant : {who} {where}.",
                         "Personne ne peut venir la défendre : c'est de l'or gratuit.",
                         stand[0][1], tier="high", score=_clamp(0.5 + 0.35 * conf + 0.1 * edge), priority=85,
                         color="safe", genius=True, life=18.0, label="TOUR",
                         factors=(f"jungler {jl.source} {jl.side} ({conf:.0%})",))
        return None
    # top commit -> the dragon side is free
    if role not in ("BOTTOM", "UTILITY", "JUNGLE", "MIDDLE"):
        return None
    if (_obj_up(ctx, "dragon") or _obj_up(ctx, "elder")) and crowd_bot < 2:
        if role != "JUNGLE" and not _ally_jungler_alive(ctx):
            return None
        key = "elder" if _obj_up(ctx, "elder") else "dragon"
        return _call("free_dragon", f"free:{key}:{int(ctx.gt // 60)}", OBJ_TITLE[key],
                     f"Le {('dragon ancestral' if key == 'elder' else 'dragon')} est libre : pousse ta vague et va-y.",
                     f"{who[0].upper() + who[1:]} {where} : ils ne peuvent pas le contester à temps.",
                     DRAGON_UV, tier="high", score=_clamp(0.55 + 0.35 * conf + 0.1 * edge), priority=86,
                     color="safe", genius=True, life=18.0, label="DRAGON",
                     factors=(f"jungler {jl.source} {jl.side} ({conf:.0%})", *why_edge))
    stand = _standing(ctx, theirs, "bot")
    if role in ("BOTTOM", "UTILITY") and stand and _zone_lane(ctx.me_uv) == "bot" and _tower_free(ctx, "bot"):
        return _call("free_dragon", f"cross:bot_tower:{int(ctx.gt // 60)}", "TOUR !",
                     f"Frappe leur tour du bas maintenant : {who} {where}.",
                     "Leur jungler ne peut pas venir vous punir : c'est le moment de pousser.",
                     stand[0][1], tier="high", score=_clamp(0.45 + 0.35 * conf), priority=84, color="safe",
                     genius=True, life=18.0, label="TOUR", factors=(f"jungler {jl.source} {jl.side}",))
    return None


def _tower_free(ctx: MacroCtx, lane: str) -> bool:
    """Can I hit their tower in ``lane`` now: my wave is there (or unknown) and, during the laning
    phase, my lane opponent(s) are not in lane (dead / not seen for a while)."""
    lw = _lane_wave(ctx, lane)
    meet = _f(_get(lw, "meet")) if lw is not None else None
    if meet is not None and meet < 0.55:
        return False
    if ctx.phase != "laning":
        return True
    for a in ctx.lane_opps:
        p = _player(ctx, a)
        if p is not None and bool(getattr(p, "is_dead", False)):
            continue
        tr = _track(ctx, a)
        if tr is None or bool(getattr(tr, "visible", False)) or (_f(getattr(tr, "hidden_s", None), 0.0) or 0.0) < 10.0:
            return False
    return True


def _ally_jungler_alive(ctx: MacroCtx) -> bool:
    for p in getattr(ctx.game, "allies", None) or []:
        if str(getattr(p, "position", "") or "").upper() == "JUNGLE" or bool(getattr(p, "has_smite", False)):
            return not bool(getattr(p, "is_dead", False))
    return True


def _rule_rotate_mid(ctx: MacroCtx) -> GeniusCall | None:
    if not ctx.me_alive or ctx.role not in ("BOTTOM", "UTILITY") or ctx.my_team is None or ctx.st is None:
        return None
    if ctx.phase == "laning" and ctx.gt < LANING_END_GT:
        return None
    if _zone_lane(ctx.me_uv) != "bot" or _obj_soon(ctx) or _obj_up(ctx, "dragon"):
        return None
    down = getattr(ctx.st, "turrets_down", frozenset()) or frozenset()
    mine = (ctx.my_team, "bot", 1) in down
    theirs = (ctx.enemy_team, "bot", 1) in down
    if not (mine or theirs):
        return None
    target = LANE_POINT[ctx.my_team]["mid"]
    if mine:
        text, why = ("Va mid : ta tour du bas est tombée.",
                     "Seul en bas tu es une proie ; au milieu tu es protégé et près des deux côtés.")
    else:
        text, why = ("Va mid avec ton duo : leur tour du bas est prise.",
                     "Ta voie est finie : au milieu vous mettez la pression sur une nouvelle tour.")
    return _call("rotate_mid", f"rotate_mid:{'mine' if mine else 'theirs'}", "VA MID", text, why, target,
                 tier="mid", score=0.6, priority=70, color="teal", life=25.0,
                 factors=("tour du bas tombée", "fin de la phase de voie"))


def _rule_side_wave(ctx: MacroCtx) -> GeniusCall | None:
    if not ctx.me_alive or ctx.phase == "laning" or ctx.role in ("UTILITY", "JUNGLE", None) or ctx.my_team is None:
        return None
    if ctx.me_uv is None or _obj_soon(ctx) or (ctx.hp is not None and ctx.hp < 0.5):
        return None
    jl = jungler_location(ctx)
    fresh = _fresh_enemies(ctx)
    best: tuple[float, str, tuple[float, float], list[str]] | None = None
    order = ("bot", "top") if ctx.role == "BOTTOM" else ("top", "bot")
    for lane in order:
        lw = _lane_wave(ctx, lane)
        if lw is None:
            continue
        n_en = int(_f(_get(lw, "enemy"), 0) or 0)
        meet = _f(_get(lw, "meet"))
        if n_en < (2 if ctx.keep else 3) or meet is None or meet > (0.6 if ctx.keep else 0.5):
            continue
        if not _standing(ctx, ctx.my_team, lane):
            continue                                       # no tower left to farm under
        point = lane_uv(lane, max(0.15, meet), ctx.my_team)
        mates = [a for a in ctx.allies if _uv(getattr(a, "uv", None)) is not None]
        if any(geometry.dist(_uv(a.uv), point) < SIDE_WAVE_EMPTY_R for a in mates):  # type: ignore[arg-type]
            continue
        if _zone_lane(ctx.me_uv) == lane:
            continue                                       # already there
        my_d = geometry.dist(ctx.me_uv, point)
        if any(geometry.dist(_uv(a.uv), point) < my_d - 0.05 for a in mates):  # type: ignore[arg-type]
            continue                                       # a mate is closer: his job
        # safe-split rule: their jungler located elsewhere / dead, or 3+ enemies seen on the other side
        opposite = {"top": "bot", "bot": "top"}.get(lane)
        other = [e for e in fresh if side_of(_uv(e.uv)) == opposite]  # type: ignore[arg-type]
        near = [e for e in fresh if geometry.dist(_uv(e.uv), point) < 0.3]  # type: ignore[arg-type]
        if near:
            continue
        jg_far = jl.dead or (jl.side is not None and jl.side != lane and jl.conf >= 0.5)
        if not (jg_far or len(other) >= 3):
            continue
        why_bits = []
        if jl.dead:
            why_bits.append("leur jungler est mort")
        elif jg_far:
            why_bits.append(f"leur jungler est {SIDE_FR.get(jl.side or '', 'loin')}")
        if len(other) >= 3:
            why_bits.append(f"{len(other)} ennemis sont de l'autre côté")
        value = (0.45 + 0.05 * min(n_en, 7) - 0.4 * my_d) * (0.75 + 0.25 * max(vision_share(ctx), jl.conf))
        if best is None or value > best[0]:
            best = (value, lane, point, why_bits)
    if best is None:
        return None
    value, lane, point, bits = best
    return _call("side_wave", f"side_wave:{lane}:{int(ctx.gt // 45)}", f"VA {LANE_FR[lane].upper()}",
                 f"Change de voie : va {LANE_FR[lane]}, la vague arrive et personne n'y est.",
                 f"Une vague = plus de 100 PO et de l'XP, sans risque : {' et '.join(bits)}.",
                 point, tier="basic", score=_clamp(value), priority=62, color="teal", life=22.0,
                 label="VAGUE", factors=tuple(bits))


def _rule_split_safe(ctx: MacroCtx) -> GeniusCall | None:
    if not ctx.me_alive or ctx.phase == "laning" or ctx.me_uv is None or ctx.my_team is None:
        return None
    lane = _zone_lane(ctx.me_uv)
    if lane not in ("top", "bot") or _obj_soon(ctx, 60.0) or (ctx.hp is not None and ctx.hp < 0.55):
        return None
    splitter = ctx.role == "TOP"
    try:
        from treeaicoach.meta import profile

        me = getattr(ctx.game, "me", None)
        splitter = splitter or profile(getattr(me, "champion_alias", "")).has("splitpush")
    except Exception:
        pass
    if not splitter:
        return None
    fresh = _fresh_enemies(ctx)
    other = [e for e in fresh if geometry.dist(_uv(e.uv), ctx.me_uv) >= 0.5]  # type: ignore[arg-type]
    near = [e for e in fresh if geometry.dist(_uv(e.uv), ctx.me_uv) < 0.3]  # type: ignore[arg-type]
    if len(other) < 3 or near:
        return None
    stand = _standing(ctx, ctx.enemy_team, lane)
    if not stand:
        return None
    jl = jungler_location(ctx)
    if not jl.dead and jl.side == lane and jl.conf >= 0.3:
        return None
    # every living enemy but one must be accounted for, their jungler included (seen far / dead):
    # a split with their jungler unseen is how a side laner gets caught
    other_aliases = {str(getattr(e, "alias", "") or "").lower() for e in other}
    alive = [str(getattr(p, "champion_alias", "") or "").lower() for p in (getattr(ctx.game, "enemies", None) or [])
             if not bool(getattr(p, "is_dead", False))]
    unaccounted = [a for a in alive if a not in other_aliases]
    jg_ok = jl.dead or (ctx.jungler_alias is not None and ctx.jungler_alias in other_aliases) \
        or (not jl.known and len(unaccounted) == 0)
    if (len(unaccounted) > (2 if ctx.keep else 1) or not jg_ok) and not ctx.keep:
        return None
    where = SIDE_FR[side_of((sum(_uv(e.uv)[0] for e in other) / len(other),  # type: ignore[index]
                             sum(_uv(e.uv)[1] for e in other) / len(other)))]  # type: ignore[index]
    edge, why_edge = team_edge(ctx)
    return _call("split_safe", f"split:{lane}:{int(ctx.gt // 60)}", "POUSSE TA VOIE",
                 f"Pousse {LANE_FR[lane]} et frappe la tour : {len(other)} ennemis sont {where}.",
                 "Ils ne peuvent pas défendre deux côtés. Recule dès qu'ils disparaissent de la carte.",
                 stand[0][1], tier="mid", score=_clamp((0.4 + 0.1 * len(other) + 0.1 * edge)
                                                       * (0.7 + 0.3 * vision_share(ctx))), priority=66,
                 color="safe", life=20.0, label="TOUR", factors=(f"{len(other)} ennemis vus loin", *why_edge))


def _rule_lane_swap(ctx: MacroCtx) -> GeniusCall | None:
    if not ctx.me_alive or ctx.phase != "laning" or not (90.0 <= ctx.gt < LANING_END_GT) or ctx.my_team is None:
        return None
    duo = [a for a, r in ctx.enemy_roles.items() if r in ("BOTTOM", "UTILITY")]
    solo = [a for a, r in ctx.enemy_roles.items() if r == "TOP"]
    if len(duo) < 2:
        return None

    def lane_seen(alias: str) -> str | None:
        tr = _track(ctx, alias)
        uv = _uv(getattr(tr, "uv", None)) if tr is not None else None
        if uv is None or (_f(getattr(tr, "hidden_s", None), 99.0) or 0.0) > 8.0:
            return None
        return _zone_lane(uv)

    duo_top = all(lane_seen(a) == "top" for a in duo)
    if not duo_top:
        return None
    solo_bot = bool(solo) and lane_seen(solo[0]) == "bot"
    if ctx.role == "TOP" and _zone_lane(ctx.me_uv) == "top":
        return _call("lane_swap", "lane_swap:top", "VA BOT", "Va bot : leur duo est en haut.",
                     "Ne reste pas seul contre deux : en bas tu prends l'XP"
                     + (f" face à {ctx.enemy_names.get(solo[0], 'leur top')} seul." if solo_bot else "."),
                     LANE_POINT[ctx.my_team]["bot"], tier="mid", score=0.6, priority=72, color="teal", life=25.0,
                     factors=("duo ennemi en haut",))
    if ctx.role in ("BOTTOM", "UTILITY") and _zone_lane(ctx.me_uv) == "bot" and solo_bot:
        stand = _standing(ctx, ctx.enemy_team, "bot")
        return _call("lane_swap", "lane_swap:bot", "2 CONTRE 1",
                     f"2 contre 1 en bas : pousse et frappe leur tour.",
                     f"Leur duo est parti en haut, {ctx.enemy_names.get(solo[0], 'leur top')} est seul face à vous.",
                     stand[0][1] if stand else None, tier="mid", score=0.6, priority=72, color="safe", life=25.0,
                     label="TOUR", factors=("duo ennemi en haut", "top ennemi en bas"))
    return None


def _rule_waves(ctx: MacroCtx) -> GeniusCall | None:
    """wave_recall / wave_freeze / back_off (my lane)."""
    lane = _my_lane(ctx)
    if not ctx.me_alive or lane is None or ctx.role in ("JUNGLE",) or ctx.in_base or ctx.me_uv is None:
        return None
    if _zone_lane(ctx.me_uv) != lane:
        return None
    support = ctx.role == "UTILITY"                   # a support does not manage the wave / recall timing
    lw = _lane_wave(ctx, lane)
    meet = _f(_get(lw, "meet")) if lw is not None else None
    state = _get(lw, "state") if lw is not None else None
    n_al = int(_f(_get(lw, "ally"), 0) or 0) if lw is not None else 0
    jl = jungler_location(ctx)
    # -- freeze: their jungler is coming to my side while my wave is pushed forward
    my_half = side_of(ctx.me_uv)
    if meet is not None and meet >= 0.55 and ctx.gt >= 150.0 and not jl.dead and jl.uv is not None \
            and jl.conf >= (0.3 if ctx.keep else 0.6) and geometry.dist(jl.uv, ctx.me_uv) < JG_NEAR_R \
            and (jl.age >= 1.0 or ctx.keep) \
            and (lane == "mid" or side_of(jl.uv) in (my_half, "mid")):
        safe = _safe_uv(ctx)
        return _call("wave_freeze", f"freeze:{int(ctx.gt // 30)}", "NE POUSSE PLUS",
                     f"Arrête de pousser, reste près de ta tour : {jl.name or 'leur jungler'} vient vers toi.",
                     "Près de ta tour un gank échoue ; loin de ta tour tu meurs.",
                     safe, tier="basic", score=_clamp(0.4 + 0.4 * jl.conf), priority=74, color="danger",
                     life=15.0, label="TA TOUR", factors=(f"jungler {jl.source} à {geometry.dist(jl.uv, ctx.me_uv):.2f}",))
    # -- back off: my lane opponent disappeared, nobody sees him, I am forward
    if ctx.phase == "laning" and ctx.gt >= 180.0 and (_forward(ctx) or (meet is not None and meet >= 0.6)):
        gone = []
        for a in ctx.lane_opps:
            p = _player(ctx, a)
            if p is None or bool(getattr(p, "is_dead", False)):
                continue
            tr = _track(ctx, a)
            hid = _f(getattr(tr, "hidden_s", None), 0.0) if tr is not None else 0.0
            if tr is not None and not bool(getattr(tr, "visible", False)) \
                    and (4.0 if ctx.keep else 8.0) <= (hid or 0.0) <= 40.0:
                uv = _uv(getattr(tr, "uv", None))
                if uv is not None and ctx.enemy_team and geometry.in_fountain(uv[0], uv[1], ctx.enemy_team):
                    continue
                gone.append(ctx.enemy_names.get(a, a))
        jg_unknown = not jl.dead and jl.conf < 0.3
        if gone and jg_unknown:
            safe = _safe_uv(ctx)
            return _call("back_off", f"back_off:{'-'.join(gone)}:{int(ctx.gt // 30)}", "RECULE",
                         f"Recule vers ta tour : {gone[0]} a disparu.",
                         "Personne ne le voit : il peut t'attendre dans la jungle avec son jungler.",
                         safe, tier="basic", score=0.5, priority=68, color="danger", life=14.0, label="TA TOUR",
                         factors=(f"{gone[0]} disparu", "jungler inconnu"))
    # -- recall timing: wave pushed into their tower + gold to spend (or low HP)
    gold_ok = ctx.gold >= RECALL_GOLD
    low = ctx.hp is not None and ctx.hp < LOW_HP
    if (gold_ok or low) and meet is not None and ctx.gt >= 150.0 and not support:
        cannon = next_cannon_arrival(ctx.gt)
        why_gold = f"{int(ctx.gold)} PO à dépenser" if gold_ok else "tu as peu de vie"
        if state == "pushing" and meet >= 0.62 and n_al >= 3:
            return _call("wave_recall", f"recall:{int(ctx.gt // 60)}", "RENTRE",
                         "Ta vague s'écrase sur leur tour : rentre maintenant.",
                         f"{why_gold} ; leurs sbires restent sous leur tour pendant ton retour.",
                         FOUNTAIN.get(ctx.my_team or ""), tier="basic", score=0.55 + (0.1 if gold_ok and low else 0.0),
                         priority=58, color="gold", life=15.0, label="BASE", factors=(why_gold, "vague poussée"))
        if state in ("even", "pushed_in") and meet < 0.55 and not low:   # low HP: never "push first"
            extra = " (la vague du canon arrive : prends-la d'abord)" if cannon <= 20.0 else ""
            return _call("wave_recall", f"recall_push:{int(ctx.gt // 60)}", "POUSSE PUIS RENTRE",
                         "Pousse ta vague puis rentre.",
                         f"{why_gold} ; rentrer avec la vague chez toi = des sbires perdus sous ta tour{extra}.",
                         lane_uv(lane, 0.65, ctx.my_team), tier="basic", score=0.45, priority=56, color="gold",
                         life=15.0, label="POUSSE", factors=(why_gold, f"canon dans {_secs(cannon)} s"))
    return None


RULES = (_rule_fight_lost, _rule_fight_won, _rule_jungler_dead, _rule_cross_map, _rule_plates, _rule_lane_swap,
         _rule_rotate_mid, _rule_split_safe, _rule_side_wave, _rule_waves)


def evaluate(ctx: MacroCtx) -> list[GeniusCall]:
    """Every call the situation supports, best first (score x priority). Pure, never raises."""
    out: list[GeniusCall] = []
    for rule in RULES:
        try:
            c = rule(ctx)
        except Exception:
            log.debug("macro rule %s failed", getattr(rule, "__name__", rule), exc_info=True)
            c = None
        if c is not None:
            out.append(replace(c, t=ctx.t))
    out.sort(key=lambda c: (-(c.priority / 100.0 + c.score), c.kind))
    return out


def level_key(level: Any) -> str:
    try:
        from treeaicoach.skill import normalize

        return normalize(level)
    except Exception:
        return "intermediaire"


# ----------------------------------------------------------------------------- planner
class MacroPlanner:
    """One active :class:`GeniusCall` at a time (see the module docstring). Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._active: GeniusCall | None = None
            self._active_since = 0.0
            self._invalid_since: float | None = None
            self._last_start: float | None = None
            self._kind_t: dict[str, float] = {}
            self._badge_t: dict[str, float] = {}
            self._recall_armed = True
            self._done: set[str] = set()
            self._last_t: float | None = None
            self.history: list[tuple[float, GeniusCall]] = []         # (gt, call) of every call shown
            self.cancelled: list[tuple[float, str, str]] = []         # (gt, kind, reason)

    def active(self) -> GeniusCall | None:
        with self._lock:
            return self._active

    def suppressed_rules(self, t: float) -> frozenset[str]:
        """coach.MapCoach rules that would repeat a recent call (``macro_tip:<rule>``)."""
        with self._lock:
            out: set[str] = set()
            for kind, t0 in self._kind_t.items():
                if 0.0 <= t - t0 < OVERLAP_S:
                    out |= OVERLAPS.get(kind, frozenset())
            return frozenset(out)

    def update(self, ctx: MacroCtx, level: Any = "intermediaire") -> MacroUpdate:
        try:
            with self._lock:
                return self._update(ctx, level_key(level))
        except Exception:
            log.exception("MacroPlanner.update failed")
            return MacroUpdate(active=self._active)

    def _cancel(self, up: MacroUpdate, ctx: MacroCtx, reason: str) -> None:
        if self._active is not None:
            up.cancelled, up.cancel_reason = self._active, reason
            self.cancelled.append((ctx.gt, self._active.kind, reason))
            del self.cancelled[:-200]
        self._active, self._invalid_since = None, None

    def _update(self, ctx: MacroCtx, level: str) -> MacroUpdate:
        up = MacroUpdate()
        t = ctx.t
        if self._last_t is not None and t < self._last_t - 5.0:          # new timeline
            self._active, self._last_start, self._kind_t, self._done, self._badge_t = None, None, {}, set(), {}
        self._last_t = t
        cfg = LEVELS.get(level, LEVELS["intermediaire"])
        # never during a fight / gank / while dead: an active call is no longer the plan
        if ctx.in_fight or ctx.threat >= 1 or not ctx.me_alive:
            if self._active is not None:
                self._cancel(up, ctx, "combat" if ctx.in_fight else "gank" if ctx.threat >= 1 else "mort")
            return up
        if ctx.in_base:
            self._recall_armed = True
        cands = evaluate(ctx)
        a = self._active
        if a is not None:
            keep = evaluate(replace(ctx, keep=True))          # hysteresis: relaxed thresholds to stay valid
            same = next((c for c in keep if c.kind == a.kind and c.ident == a.ident), None) or \
                next((c for c in keep if c.kind == a.kind), None)
            if same is None:
                if self._invalid_since is None:
                    self._invalid_since = t
                if t - self._invalid_since >= GRACE_S:
                    self._cancel(up, ctx, "invalide")
            else:
                self._invalid_since = None
            if self._active is not None and t - self._active_since >= self._active.life_s:
                self._active, self._invalid_since = None, None                # done (expired)
        if self._active is not None and t - self._active_since < HOLD_S:
            up.active = self._active
            return up
        for c in cands:
            if c.tier not in cfg["tiers"] or c.score < cfg["min_score"] or c.ident in self._done:
                continue
            if c.color == "safe" and c.kind not in POST_FIGHT_KINDS and (
                    (ctx.stance_score is not None and ctx.stance_score <= STANCE_SAFE_BLOCK)
                    or (ctx.hp is not None and ctx.hp < LOW_HP)):
                continue                                   # the gauge says SAFE / I am low: no "go" call
            if c.kind == "wave_recall" and not self._recall_armed:
                continue                                   # once per trip: wait for a base visit
            if self._active is not None:
                if c.kind == self._active.kind or c.score < self._active.score + PREEMPT_MARGIN:
                    continue
            last_k = self._kind_t.get(c.kind)
            if last_k is not None and t - last_k < KIND_COOLDOWN_S.get(c.kind, 60.0):
                continue
            if c.kind not in URGENT_KINDS and self._last_start is not None and t - self._last_start < cfg["gap"]:
                continue
            if self._active is not None:
                self._cancel(up, ctx, "remplacé")
            if c.genius:
                last_b = self._badge_t.get(c.kind)
                if c.score < BADGE_MIN_SCORE or (last_b is not None and t - last_b < BADGE_KIND_GAP_S):
                    c = replace(c, genius=False)
                else:
                    self._badge_t[c.kind] = t
            self._active, self._active_since, self._invalid_since = c, t, None
            if c.kind == "wave_recall":
                self._recall_armed = False
            self._last_start = t
            self._kind_t[c.kind] = t
            self._done.add(c.ident)
            self.history.append((ctx.gt, c))
            del self.history[:-200]
            up.new = c
            break
        up.active = self._active
        return up


def ai_plan(call: GeniusCall | None) -> dict[str, Any] | None:
    """The active call in :func:`treeaicoach.ai_advisor.parse_plan` format (offline plan)."""
    if call is None:
        return None
    return {"plan": call.text, "etapes": [call.why], "objectif": None, "urgence": "haute" if call.genius else "moyenne"}


__all__ = ["MacroPlanner", "MacroCtx", "MacroUpdate", "GeniusCall", "JunglerLoc", "build_ctx", "evaluate",
           "jungler_location", "team_edge", "lane_uv", "next_cannon_arrival", "is_cannon_wave", "wave_number",
           "LEVELS", "OVERLAPS", "ai_plan", "vision_share"]
