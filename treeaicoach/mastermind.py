"""MASTERMIND: the game-understanding layer (compositions, power timeline, win conditions, threats).

Real request: "I want the app to be a real mastermind that captures everything, with all its
metadata plus the analysis". A coach looks at the ten champions and says, before anything else:
what each team wants to do, who is stronger NOW and when that flips, how each team wins, what
MY job is in our plan, and who on their side will kill me. This module is that read, rebuilt
from the public data at every coaching tick (1 Hz, well under 1 ms):

* :func:`analyze` (pure, single-entry cache) reads a Live Client :class:`GameInfo` (the ten
  champions, levels, items of the Tab screen, K/D/A, my resistances) with the champion profiles
  (:mod:`treeaicoach.meta`: class, range, power curve, level-6 spike, engage / poke / pick / dive /
  splitpush styles, waveclear, sustain) and the item effects (:mod:`treeaicoach.itemization`) and
  returns a :class:`Reading`:

  - :class:`Comp` per team: identity (engage / poke / pick / dive / split / siege) with the
    champions behind it, tempo (early / mid / scaling), damage profile (AD / AP / true share),
    frontline / backline, peel, waveclear, the carry;
  - :class:`Window` for the team, my lane and the two junglers: who is stronger now (power curve
    of every champion x resources: item gold + levels + ultimate spikes) and when it flips (the
    curves projected with today's gold / level gap), e.g. ``Ton équipe est plus forte jusqu'à
    ~22:00 : force les objectifs`` or ``Joue le temps : ton équipe est plus forte après ~26:00``;
  - win conditions per team in plain French (3 at most) and MY role in our plan
    (``Toi : seul tank de l'équipe, tiens le top, TP sur les dragons``);
  - the threat ranking (carry, most fed, most dangerous to me: damage type vs my resistances,
    crowd control, burst, mobility) with the enemy key item effects
    (:func:`treeaicoach.itemization.enemy_effects`).

* :class:`MastermindModel` (one per engine / game) keeps the latest reading, the history of the
  windows (post-game "what the team did vs the plan"), and the voice budget of the window calls
  (at most one spoken window per :data:`VOICE_GAP_S`). The engine registers the live model with
  :func:`set_active`; :func:`for_ctx` gives it to the game-changer rule
  (:func:`treeaicoach.game_changers.rule_power_window`) without touching the planner.

Consumers: the play gauge (:meth:`Reading.gauge_factors`: curve-only reasons, the gold / level
gaps are already counted by :func:`treeaicoach.coach.stance_factors`), the game changers (one
card line at a time through the macro planner + presenter), the game-start team plan
(:func:`treeaicoach.game_plan.team_card`), the AI snapshot (:meth:`Reading.snapshot`) and the
post-game report (:func:`understanding`: comps, windows, what the team did vs the plan).

Riot policy: public Live Client data only (Tab items / levels / scores, my own stats). No
cooldown, summoner spell timer or hidden information. Pure Python, never raises from its public
functions.
"""

from __future__ import annotations

import logging
import math
import threading
import weakref
from dataclasses import dataclass, field
from typing import Any, Iterable

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------- tunables
#: relative strength of a power curve at minute m (piecewise linear between these points)
CURVE_POINTS: dict[str, tuple[tuple[float, float], ...]] = {
    "early": ((0.0, 1.10), (10.0, 1.10), (20.0, 1.0), (30.0, 0.92), (45.0, 0.90)),
    "mid": ((0.0, 0.97), (10.0, 1.02), (20.0, 1.04), (30.0, 1.0), (45.0, 0.98)),
    "late": ((0.0, 0.88), (10.0, 0.92), (20.0, 1.0), (30.0, 1.10), (45.0, 1.14)),
}
LEVEL_GOLD = 250.0               # resource value of one level (gold equivalent)
ULT_GOLD = {6: 150.0, 11: 200.0, 16: 150.0}   # rank-up bonus (level 6: x spike6 1..3)
EVEN = 0.035                     # |edge| below this: even
CLEAR = 0.06                     # |edge| from this: a clear window (card material)
HORIZON_MIN = 40                 # projection horizon (game minute)
LANING_END_GT = 840.0            # 14:00
VOICE_GAP_S = 180.0              # at most one spoken window every 3 min
CACHE_KEEP_S = 10.0              # a registered model is valid for a planner ctx this close in game time
IDENTITIES = ("engage", "pick", "poke", "dive", "split", "siege")
IDENTITY_FR = {"engage": "engage", "pick": "attrapes (pick)", "poke": "harcèlement (poke)",
               "dive": "plongée (dive)", "split": "poussée de côté (split)", "siege": "siège",
               "teamfight": "combat d'équipe"}
TEMPO_FR = {"early": "fort tôt", "mid": "fort en milieu de partie", "scaling": "fort en fin de partie"}
EFFECT_FR = {"antiheal": "anti-soin", "stasis": "stase (Zhonya)", "armorpen": "pénétration d'armure",
             "magicpen": "pénétration magique", "lifesteal": "vol de vie", "shield": "bouclier",
             "tenacity": "ténacité", "armor": "armure", "mr": "résistance magique", "cleanse": "purge",
             "anticrit": "anti-critique", "pcthp": "dégâts en % des PV", "health": "PV"}
#: effects worth naming in a threat line (the plain stats are not news)
KEY_EFFECTS = ("stasis", "antiheal", "armorpen", "magicpen", "shield", "lifesteal", "cleanse", "pcthp")
FRONT_CLASSES = frozenset({"TANK", "VANGUARD", "WARDEN", "JUGGERNAUT"})
CARRY_CLASSES = frozenset({"MARKSMAN", "MAGE", "ASSASSIN", "BURST", "ARTILLERY", "SKIRMISHER", "BATTLEMAGE"})
LANE_OF_ROLE = {"TOP": "top", "JUNGLE": "jungle", "MIDDLE": "mid", "BOTTOM": "bot", "UTILITY": "bot"}
CARD_MAX = 60                    # ux judge: a card line is at most 60 characters


def _f(x: Any, d: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return d
    return v if math.isfinite(v) else d


def clock(gt: float | None) -> str:
    """``"22:00"`` (rounded to the minute)."""
    if gt is None:
        return ""
    m = int(round(max(0.0, float(gt)) / 60.0))
    return f"{m}:00"


def _curve_exact(curve: str, minutes: float) -> float:
    pts = CURVE_POINTS.get(curve, CURVE_POINTS["mid"])
    m = max(0.0, float(minutes))
    if m >= pts[-1][0]:
        return pts[-1][1]
    for (m0, v0), (m1, v1) in zip(pts, pts[1:]):
        if m0 <= m <= m1:
            return v0 + (v1 - v0) * (m - m0) / (m1 - m0)
    return 1.0


#: curve value per whole minute (0..60): the projection reads this table
_TABLE: dict[str, tuple[float, ...]] = {c: tuple(_curve_exact(c, m) for m in range(61)) for c in CURVE_POINTS}


def curve_value(curve: str, minutes: float) -> float:
    """Strength of a power curve ("early" / "mid" / "late") at ``minutes`` (piecewise linear)."""
    tab = _TABLE.get(curve) or _TABLE["mid"]
    m = max(0.0, float(minutes))
    i = int(m)
    if i >= 60:
        return tab[60]
    return tab[i] + (tab[i + 1] - tab[i]) * (m - i)


def expected_resource(minutes: float) -> float:
    """Typical resource (item gold + level value) of one player at ``minutes``: the scale of a gap."""
    return 800.0 + 520.0 * max(0.0, float(minutes))


# ----------------------------------------------------------------------------- units
@dataclass(frozen=True)
class Unit:
    """One champion as the mastermind sees it."""

    alias: str
    name: str
    team: str
    side: str                      # "us" | "them"
    role: str                      # TOP / JUNGLE / MIDDLE / BOTTOM / UTILITY / ""
    meta: Any                      # meta.ChampMeta
    level: int = 1
    items: tuple[int, ...] = ()
    item_gold: int = 0
    legendaries: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    dead: bool = False
    is_me: bool = False
    defensive: int = 0             # completed items with armour / magic resist
    effects: tuple[tuple[str, int], ...] = ()
    teleport: bool = False

    @property
    def resource(self) -> float:
        lvl = max(1, min(18, int(self.level)))
        r = float(self.item_gold) + LEVEL_GOLD * (lvl - 1)
        if lvl >= 6:
            r += ULT_GOLD[6] * max(1, min(3, int(getattr(self.meta, "spike6", 2) or 2)))
        if lvl >= 11:
            r += ULT_GOLD[11]
        if lvl >= 16:
            r += ULT_GOLD[16]
        return r

    @property
    def curve(self) -> str:
        return str(getattr(self.meta, "curve", "mid") or "mid")

    @property
    def classes(self) -> frozenset[str]:
        return frozenset(getattr(self.meta, "classes", ()) or ())

    def has(self, style: str) -> bool:
        try:
            return bool(self.meta.has(style))
        except Exception:
            return False

    @property
    def frontline(self) -> bool:
        r = getattr(self.meta, "ratings", (2, 2, 2, 2, 1))
        return int(r[1]) >= 3 or bool(self.classes & FRONT_CLASSES) or self.defensive >= 2

    @property
    def ranged(self) -> bool:
        return bool(getattr(self.meta, "ranged", False))


_PROFILES: dict[str, Any] = {}
_PROFILES_SRC: list[Any] = [None]


def _profile(alias: str) -> Any:
    """meta.profile, memoised while the bundled table is the same object (meta.reset clears it)."""
    from treeaicoach import meta

    src = getattr(meta, "_DATA", None)
    if src is None or src is not _PROFILES_SRC[0]:
        _PROFILES.clear()
        p = meta.profile(alias)
        _PROFILES_SRC[0] = getattr(meta, "_DATA", None)
        _PROFILES[alias] = p
        return p
    p = _PROFILES.get(alias)
    if p is None:
        p = _PROFILES[alias] = meta.profile(alias)
    return p


def _split(u: "Unit") -> tuple[float, float, float]:
    """(physical, magic, true) damage share of a unit (itemization.damage_split from its profile)."""
    try:
        from treeaicoach.itemization import AP, MIXED, TRUE_DMG

        m = u.meta
        dt = m.damage if getattr(m, "known", False) and m.damage in ("P", "M", "X") else (
            "M" if u.alias in AP else "X" if u.alias in MIXED else "P")
    except Exception:
        return 0.5, 0.5, 0.0
    ad, ap = (0.1, 0.9) if dt == "M" else (0.5, 0.5) if dt == "X" else (0.9, 0.1)
    tr = 0.25 if u.alias in TRUE_DMG else 0.0
    return ad * (1 - tr), ap * (1 - tr), tr


def _unit(p: Any, side: str, is_me: bool = False) -> Unit | None:
    alias = str(getattr(p, "champion_alias", "") or "")
    if not alias:
        return None
    items = tuple(int(i) for i in (getattr(p, "items", None) or ()) if isinstance(i, int) and not isinstance(i, bool))
    gold = legend = defensive = 0
    effects: dict[str, int] = {}
    try:
        from treeaicoach.itemization import inventory_effects, load_items

        table = load_items()
        for i in items:
            it = table.get(i)
            if it is None:
                continue
            gold += int(getattr(it, "gold", 0) or 0)
            if it.kind == "legendary":
                legend += 1
                if "Armor" in it.tags or "SpellBlock" in it.tags:
                    defensive += 1
        effects = inventory_effects(items, legendary_only=True)
    except Exception:
        log.debug("mastermind items failed", exc_info=True)
    sc = getattr(p, "scores", None) or {}
    spells = " ".join(str(s) for s in (tuple(getattr(p, "spell_ids", ()) or ()) + tuple(getattr(p, "spells", ()) or ())))
    meta = _profile(alias)
    return Unit(alias=alias, name=str(getattr(p, "champion_name", "") or getattr(meta, "name", "") or alias),
                team=str(getattr(p, "team", "") or ""), side=side,
                role=str(getattr(p, "position", "") or "").upper(), meta=meta,
                level=int(_f(getattr(p, "level", 1), 1) or 1), items=items, item_gold=gold, legendaries=legend,
                kills=int(_f(sc.get("kills"))), deaths=int(_f(sc.get("deaths"))), assists=int(_f(sc.get("assists"))),
                dead=bool(getattr(p, "is_dead", False)), is_me=is_me, defensive=defensive,
                effects=tuple(sorted(effects.items())),
                teleport=("Teleport" in spells or "Téléportation" in spells))


def units_of(game: Any) -> tuple[list[Unit], list[Unit]]:
    """``(our units, their units)`` of a Live Client snapshot (me first). Never raises."""
    us: list[Unit] = []
    them: list[Unit] = []
    try:
        me = getattr(game, "me", None)
        if me is not None:
            u = _unit(me, "us", True)
            if u is not None:
                us.append(u)
        for p in getattr(game, "allies", None) or []:
            u = _unit(p, "us")
            if u is not None:
                us.append(u)
        for p in getattr(game, "enemies", None) or []:
            u = _unit(p, "them")
            if u is not None:
                them.append(u)
    except Exception:
        log.debug("mastermind units failed", exc_info=True)
    return us, them


# ----------------------------------------------------------------------------- compositions
@dataclass(frozen=True)
class Comp:
    side: str                                     # "us" | "them"
    identity: tuple[str, ...]                     # ("poke", "siege") ; ("teamfight",) when nothing stands out
    scores: dict[str, float]
    by: dict[str, tuple[str, ...]]                # identity -> champion names behind it
    tempo: str                                    # "early" | "mid" | "scaling"
    ad: float
    ap: float
    true: float
    frontline: tuple[str, ...]
    backline: tuple[str, ...]
    peel: float
    waveclear: float
    carry: str | None                             # champion name
    carry_alias: str | None

    @property
    def damage(self) -> str:
        if self.ap >= 0.62:
            return "magique"
        if self.ad >= 0.62:
            return "physique"
        return "mixte"

    def summary(self) -> str:
        """``Harcèlement (poke) + siège · fort en fin de partie · dégâts surtout magiques · devant : Garen``."""
        ident = " + ".join(IDENTITY_FR.get(i, i) for i in self.identity)
        dmg = {"magique": "dégâts surtout magiques", "physique": "dégâts surtout physiques"}.get(self.damage,
                                                                                                   "dégâts mixtes")
        front = ", ".join(self.frontline) if self.frontline else "aucune ligne de front"
        return f"{ident[:1].upper()}{ident[1:]} · {TEMPO_FR.get(self.tempo, self.tempo)} · {dmg} · devant : {front}"

    def to_dict(self) -> dict[str, Any]:
        return {"identite": list(self.identity), "par": {k: list(v) for k, v in self.by.items() if v},
                "tempo": self.tempo, "degats": self.damage,
                "ad_ap_brut": [round(self.ad, 2), round(self.ap, 2), round(self.true, 2)],
                "devant": list(self.frontline), "derriere": list(self.backline), "protection": round(self.peel, 1),
                "nettoyage": round(self.waveclear, 1), "carry": self.carry, "resume": self.summary()}


def comp_of(units: Iterable[Unit], side: str = "us") -> Comp:
    """Composition identity of a team (profiles + current items). Pure."""
    us = list(units)
    scores = dict.fromkeys(IDENTITIES, 0.0)
    by: dict[str, list[tuple[float, str]]] = {k: [] for k in IDENTITIES}
    ad = ap = tr = wsum = 0.0
    peel = wave = 0.0
    late = early = 0
    for u in us:
        m, cls = u.meta, u.classes
        contrib = {
            "engage": (1.0 if u.has("engage") else 0.0) + (0.5 if "VANGUARD" in cls else 0.0),
            "pick": (1.0 if u.has("pick") else 0.0) + (0.5 if "CATCHER" in cls else 0.0)
            + (0.4 if "ASSASSIN" in cls else 0.0),
            "poke": (1.0 if u.has("poke") else 0.0) + (0.5 if "ARTILLERY" in cls else 0.0),
            "dive": (1.0 if u.has("dive") else 0.0) + (0.5 if "DIVER" in cls else 0.0),
            "split": (1.0 if int(getattr(m, "splitpush", 1)) >= 3 else 0.3 if int(getattr(m, "splitpush", 1)) == 2
                      else 0.0) + (0.5 if u.has("splitpush") else 0.0),
            "siege": (0.5 if u.has("poke") else 0.0) + (0.4 if int(getattr(m, "waveclear", 2)) >= 3 else 0.0)
            + (0.4 if "ARTILLERY" in cls else 0.0)
            + (0.3 if "MARKSMAN" in cls and int(getattr(m, "attack_range", 125)) >= 550 else 0.0),
        }
        for k, v in contrib.items():
            if v > 0:
                scores[k] += v
                by[k].append((v, u.name))
        a, p, t = _split(u)
        w = 1.0 + int(getattr(m, "ratings", (2,))[0]) + 0.5 * u.legendaries
        if u.frontline and not (cls & CARRY_CLASSES):
            w *= 0.6
        ad, ap, tr, wsum = ad + w * a, ap + w * p, tr + w * t, wsum + w
        peel += (1.0 if u.has("peel") else 0.0) + (0.7 if cls & {"ENCHANTER", "WARDEN"} else 0.0)
        wave += float(getattr(m, "waveclear", 2) or 2)
        late += u.curve == "late"
        early += u.curve == "early"
    ranked = sorted(((v, k) for k, v in scores.items()), reverse=True)
    ident = tuple(k for v, k in ranked if v >= 1.5)[:2] or (tuple(k for v, k in ranked[:1] if v >= 1.0)
                                                             or ("teamfight",))
    tempo = "scaling" if late - early >= 2 else "early" if early - late >= 2 else "mid"
    carry = None
    best = -1.0
    for u in us:
        m = u.meta
        c = (int(getattr(m, "ratings", (2,))[0]) + (1.0 if u.classes & CARRY_CLASSES else 0.0)
             + (0.5 if u.role in ("BOTTOM", "MIDDLE") else 0.0) + (0.4 if u.curve == "late" else 0.0)
             + 0.6 * u.legendaries + 0.15 * (u.kills - u.deaths) - (1.5 if u.role == "UTILITY" else 0.0))
        if c > best:
            best, carry = c, u
    n = max(1, len(us))
    return Comp(side=side, identity=ident, scores={k: round(v, 2) for k, v in scores.items()},
                by={k: tuple(nm for _v, nm in sorted(v, reverse=True)) for k, v in by.items()}, tempo=tempo,
                ad=ad / wsum if wsum else 0.5, ap=ap / wsum if wsum else 0.5, true=tr / wsum if wsum else 0.0,
                frontline=tuple(u.name for u in us if u.frontline),
                backline=tuple(u.name for u in us if u.ranged and not u.frontline),
                peel=peel, waveclear=wave / n, carry=carry.name if carry else None,
                carry_alias=carry.alias if carry else None)


# ----------------------------------------------------------------------------- power timeline
def _mix(units: list[Unit]) -> tuple[tuple[str, float], ...]:
    """Share of each power curve in a group of units."""
    n = {"early": 0, "mid": 0, "late": 0}
    for u in units:
        c = u.curve
        n[c if c in n else "mid"] += 1
    k = max(1, len(units))
    return tuple((c, v / k) for c, v in n.items() if v)


def _mix_value(mix: tuple[tuple[str, float], ...], m: float) -> float:
    return sum(w * curve_value(c, m) for c, w in mix) if mix else 1.0


def _curve_mean(units: list[Unit], m: float) -> float:
    return _mix_value(_mix(units), m) if units else 1.0


def edge_at(us: list[Unit], them: list[Unit], m: float, res_gap: float | None = None) -> float:
    """Power edge of ``us`` over ``them`` at minute ``m`` (> 0: we are stronger): mean curve
    difference + the resource gap (item gold + levels, today's value unless ``res_gap``) relative
    to the expected resources at ``m``. Pure."""
    if not us or not them:
        return 0.0
    if res_gap is None:
        res_gap = sum(u.resource for u in us) / len(us) - sum(u.resource for u in them) / len(them)
    return _curve_mean(us, m) - _curve_mean(them, m) + res_gap / expected_resource(m)


@dataclass(frozen=True)
class Window:
    """Who is stronger in a scope now, and until when."""

    scope: str                     # "team" | "lane" | "jungle"
    leader: str                    # "us" | "them" | "even"
    edge: float                    # > 0 we are stronger (same scale as :func:`edge_at`)
    curve_edge: float              # the power-curve part only (no gold / levels)
    until: float | None            # game time the lead flips (None: holds over the horizon)
    then: str                      # leader after the flip ("us" / "them" / "even")
    who: str = ""                  # "Vladimir" (lane), "Kindred" (jungle), "" (team)
    mine: str = ""                 # my champion / our jungler name
    text: str = ""                 # plain French read ("Ton équipe est plus forte jusqu'à ~22:00 : force les objectifs")

    @property
    def clear(self) -> bool:
        return abs(self.edge) >= CLEAR

    @property
    def key(self) -> str:
        return f"{self.scope}:{self.leader}:{int(self.until // 60) if self.until else 'x'}"

    def to_dict(self) -> dict[str, Any]:
        return {"qui": {"us": "nous", "them": "eux", "even": "égal"}[self.leader], "ecart": round(self.edge, 3),
                "jusqua": clock(self.until) if self.until else None,
                "puis": {"us": "nous", "them": "eux", "even": "égal"}[self.then] if self.until else None,
                "texte": self.text}


def project(us: list[Unit], them: list[Unit], gt: float, horizon: int = HORIZON_MIN) -> list[tuple[int, float]]:
    """``[(minute, edge)]`` from now to ``horizon`` with today's resource gap. Pure."""
    m0 = max(0.0, gt / 60.0)
    if not us or not them:
        return []
    gap = sum(u.resource for u in us) / len(us) - sum(u.resource for u in them) / len(them)
    a, b = _mix(us), _mix(them)
    out = [(int(m0), _mix_value(a, m0) - _mix_value(b, m0) + gap / expected_resource(m0))]
    for m in range(int(m0) + 1, max(int(m0) + 2, horizon + 1)):
        out.append((m, _mix_value(a, m) - _mix_value(b, m) + gap / expected_resource(m)))
    return out


def _lead(e: float) -> str:
    return "us" if e >= EVEN else "them" if e <= -EVEN else "even"


def window_of(scope: str, us: list[Unit], them: list[Unit], gt: float, who: str = "", mine: str = "",
              curve: list[tuple[int, float]] | None = None) -> Window:
    """The :class:`Window` of a scope (see the module docstring); ``curve``: its projection when
    already computed. Pure."""
    m0 = max(0.0, gt / 60.0)
    curve = project(us, them, gt) if curve is None else curve
    if not curve:
        return Window(scope, "even", 0.0, 0.0, None, "even", who, mine)
    e0 = curve[0][1]
    lead = _lead(e0)
    until = None
    then = lead
    for i, (m, e) in enumerate(curve[1:], 1):
        lv = _lead(e)
        if lv != lead and (lead != "even" or abs(e) >= CLEAR):
            # a clear change (out of "even" it must be a real lead, not noise); "then" is the next
            # real leader (them -> even -> us reads "until 19:00, then us")
            until, then = float(m) * 60.0, lv
            if lv == "even":
                then = next((_lead(e2) for _m2, e2 in curve[i:] if _lead(e2) not in ("even", lead)), "even")
            break
    w = Window(scope, lead, round(e0, 4), round(_curve_mean(us, m0) - _curve_mean(them, m0), 4), until, then, who,
               mine)
    return Window(w.scope, w.leader, w.edge, w.curve_edge, w.until, w.then, w.who, w.mine, _window_text(w))


def _window_text(w: Window) -> str:
    """Plain French read of a window (tutoiement; "ton équipe")."""
    t = clock(w.until)
    if w.scope == "team":
        if w.leader == "us":
            return (f"Ton équipe est plus forte jusqu'à ~{t} : force les objectifs" if w.until
                    else "Ton équipe est plus forte : force les objectifs")
        if w.leader == "them":
            return (f"Ils sont plus forts jusqu'à ~{t} : joue le temps, défends" if w.until and w.then == "us"
                    else f"Ils sont plus forts jusqu'à ~{t} : défends et attends" if w.until
                    else "Ils sont plus forts : défends sous tes tours, attends leur erreur")
        if w.until and w.then == "us":
            return f"Équilibré : ton équipe sera plus forte après ~{t}"
        if w.until and w.then == "them":
            return f"Équilibré : ils seront plus forts après ~{t}, force avant"
        return "Équilibré : la partie se jouera sur les erreurs"
    who = w.who or "ton adversaire"
    if w.scope == "lane":
        if w.leader == "us":
            return (f"Tu es plus fort que {who} jusqu'à ~{t} : punis-le" if w.until
                    else f"Tu es plus fort que {who} : punis-le")
        if w.leader == "them":
            return (f"{who} est plus fort jusqu'à ~{t} : évite les échanges" if w.until
                    else f"{who} est plus fort : évite les échanges, farme")
        if w.until:
            return (f"Égalité avec {who} : tu deviens plus fort après ~{t}" if w.then == "us"
                    else f"Égalité avec {who} : il devient plus fort après ~{t}")
        return f"Égalité avec {who}"
    mine = w.mine or "ton jungler"
    if w.leader == "us":
        return f"{mine} gagne les duels de jungle contre {who}" + (f" jusqu'à ~{t}" if w.until else "")
    if w.leader == "them":
        return f"{who} gagne les duels de jungle contre {mine}" + (f" jusqu'à ~{t}" if w.until else "")
    return f"Jungles à égalité ({mine} / {who})"


# ----------------------------------------------------------------------------- threats
@dataclass(frozen=True)
class Threat:
    alias: str
    name: str
    role: str
    score: float
    tags: tuple[str, ...]          # "carry", "le plus avancé", "dangereux pour toi", "ton adversaire"
    reasons: tuple[str, ...]
    effects: tuple[str, ...]       # key item effects in French ("stase (Zhonya)", "anti-soin")

    def line(self) -> str:
        why = ", ".join(self.reasons[:2])
        return f"{self.name} : {why}" if why else self.name

    def to_dict(self) -> dict[str, Any]:
        return {"c": self.name, "role": self.role, "score": round(self.score, 2), "tags": list(self.tags),
                "pourquoi": list(self.reasons[:3]), "objets": list(self.effects)}


def threats_of(them: list[Unit], me: Unit | None, game: Any = None, gt: float = 0.0,
               lane_opp: str | None = None) -> list[Threat]:
    """Enemies ranked by danger (carry potential, how fed, danger to ME), most dangerous first. Pure."""
    if not them:
        return []
    m = gt / 60.0
    avg_gold = sum(u.item_gold for u in them) / len(them)
    stats = getattr(game, "champion_stats", None) or {}
    my_armor, my_mr = _f(stats.get("armor"), 0.0), _f(stats.get("magicResist"), 0.0)
    rows = []
    for u in them:
        r = getattr(u.meta, "ratings", (2, 2, 2, 2, 1))
        reasons: list[str] = []
        carry = (int(r[0]) / 3.0) * (1.25 if u.classes & CARRY_CLASSES else 0.8) * curve_value(u.curve, m)
        if u.role == "UTILITY":
            carry *= 0.6
        lead = u.kills - u.deaths
        gold_rel = (u.item_gold / avg_gold) if avg_gold > 300 else 1.0
        fed = max(0.0, 0.15 * lead) + max(0.0, gold_rel - 1.0)
        if lead >= 3 or (u.kills >= 4 and lead >= 2):
            reasons.append(f"{u.kills}/{u.deaths}/{u.assists}")
        if u.legendaries >= 2 and gold_rel >= 1.2:
            reasons.append(f"{u.legendaries} gros objets")
        vs_me = 0.0
        a, p, _t = _split(u)
        if me is not None:
            if p >= 0.6 and (my_mr and my_armor and my_mr < 0.8 * my_armor):
                vs_me += 0.4
                reasons.append("dégâts magiques, ta résistance magique est basse")
            elif a >= 0.6 and (my_mr and my_armor and my_armor < 0.8 * my_mr):
                vs_me += 0.4
                reasons.append("dégâts physiques, ton armure est basse")
            if int(getattr(u.meta, "cc", 1)) >= 3:
                vs_me += 0.3
                reasons.append("beaucoup de contrôle")
            if u.has("burst"):
                vs_me += 0.3
                reasons.append("tue d'un coup (burst)")
            if u.has("dive") or int(r[3]) >= 3:
                vs_me += 0.2
                if len(reasons) < 3:
                    reasons.append("très mobile")
            if lane_opp and u.alias.lower() == str(lane_opp).lower():
                vs_me += 0.5
            if u.role == "JUNGLE" and gt < 900.0 and me.role != "JUNGLE":
                vs_me += 0.3
        if int(getattr(u.meta, "sustain", 1)) >= 3:
            reasons.append("se soigne beaucoup")
        if lane_opp and u.alias.lower() == str(lane_opp).lower() and not reasons:
            reasons.append("ton adversaire de voie")
        if not reasons and int(r[0]) >= 3:
            reasons.append("gros dégâts")
        effects = tuple(EFFECT_FR[e] for e, _n in u.effects if e in KEY_EFFECTS)
        rows.append((carry, fed, vs_me, u, tuple(dict.fromkeys(reasons)), effects))
    best_c = max(rows, key=lambda x: x[0])[3].alias
    best_f = max(rows, key=lambda x: x[1])
    best_v = max(rows, key=lambda x: x[2])
    out = []
    for carry, fed, vs_me, u, reasons, effects in rows:
        tags = []
        if u.alias == best_c:
            tags.append("carry")
        if u is best_f[3] and fed >= 0.3:
            tags.append("le plus avancé")
        if u is best_v[3] and vs_me >= 0.5:
            tags.append("dangereux pour toi")
        if lane_opp and u.alias.lower() == str(lane_opp).lower():
            tags.append("ton adversaire")
        out.append(Threat(u.alias, u.name, u.role, round(carry + 1.2 * fed + vs_me, 3), tuple(tags), reasons,
                          effects))
    out.sort(key=lambda x: -x.score)
    return out


# ----------------------------------------------------------------------------- win conditions
def _names(xs: Iterable[str], n: int = 2) -> str:
    xs = [x for x in xs if x][:n]
    if len(xs) == 2 and any(" et " in x for x in xs):
        return ", ".join(xs)                     # "Nunu et Willump, Wukong"
    return ", ".join(xs[:-1]) + " et " + xs[-1] if len(xs) > 1 else "".join(xs)


def win_conditions(us: Comp, them: Comp, team: Window) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(ours, theirs)``: 3 lines at most each, plain French (ours: instructions, tutoiement;
    theirs: what they want). Pure."""
    t = clock(team.until)
    ours: list[str] = []
    if team.leader == "us":
        ours.append(f"Force les objectifs avant ~{t} : ton équipe domine" if team.until
                    else "Force les objectifs : ton équipe est plus forte")
    elif team.leader == "them" and team.until and team.then == "us":
        ours.append(f"Joue le temps : ton équipe est plus forte après ~{t}")
    elif team.leader == "them" and team.until:
        ours.append(f"Défends jusqu'à ~{t} : ils sont plus forts avant")
    elif team.leader == "them":
        ours.append("Défends sous tes tours : attends leur erreur")
    elif team.until and team.then == "us":
        ours.append(f"Joue le temps : ton équipe devient plus forte après ~{t}")
    elif team.until and team.then == "them":
        ours.append(f"Force tôt : ils deviennent plus forts après ~{t}")
    ident = us.identity[0] if us.identity else "teamfight"
    names = _names(us.by.get(ident, ()))
    their_carry = them.carry or "leur carry"
    our_carry = us.carry or "ton carry"
    line = {"engage": f"Engage à 5 sur les objectifs avec {names}",
            "poke": f"Harcèle avant chaque objectif avec {names}",
            "pick": f"Attrape les ennemis isolés avec {names}",
            "dive": f"Plonge sur {their_carry} avec {names}",
            "split": f"Pousse les voies de côté avec {names}",
            "siege": "Assiège les tours : ton équipe nettoie vite les vagues",
            }.get(ident, f"Groupe-toi autour de {our_carry} pour les combats")
    ours.append(line)
    tid = them.identity[0] if them.identity else "teamfight"
    tn = _names(them.by.get(tid, ()))
    counter = {"engage": f"Ne te mets pas en ligne : ils engagent ({tn})",
               "poke": f"Engage vite ou recule : ils harcèlent de loin ({tn})",
               "pick": f"Avance groupé avec vision : ils attrapent les isolés ({tn})",
               "dive": f"Protège {our_carry} : ils plongent ({tn})",
               "split": f"Envoie un joueur contre {_names(them.by.get('split', ()), 1)} : il pousse seul",
               "siege": "Défends tes tours avec les vagues : ils assiègent",
               }.get(tid, f"Tue {their_carry} en premier")
    ours.append(counter)
    theirs: list[str] = []
    if team.leader == "them":
        theirs.append(f"Forcer les objectifs avant ~{t}" if team.until else "Forcer les combats : ils sont devant")
    elif team.leader == "us" and team.until and team.then == "them":
        theirs.append(f"Jouer le temps : plus forts après ~{t}")
    elif team.until and team.then == "them":
        theirs.append(f"Jouer le temps : plus forts après ~{t}")
    theirs.append({"engage": f"Engager à 5 avec {tn}", "poke": f"Harceler avant les objectifs ({tn})",
                   "pick": f"Attraper un isolé ({tn})", "dive": f"Plonger sur {our_carry} ({tn})",
                   "split": f"Pousser seul en voie de côté ({_names(them.by.get('split', ()), 1)})",
                   "siege": "Assiéger les tours"}.get(tid, f"Faire jouer {their_carry}"))
    theirs.append(f"Faire jouer {their_carry}")
    return tuple(dict.fromkeys(ours))[:3], tuple(dict.fromkeys(theirs))[:3]


def role_line(me: Unit | None, us: Comp, them: Comp, team: Window, lane: Window | None,
              allies: list[Unit]) -> str:
    """MY job in our plan, one line: ``Toi : seul tank de l'équipe, tiens le top, TP sur les dragons``."""
    if me is None:
        return ""
    parts: list[str] = []
    role = me.role
    if me.frontline and len(us.frontline) <= 1:
        parts.append("seul tank de l'équipe, ouvre les combats")
    elif me.has("engage") and role in ("TOP", "JUNGLE", "UTILITY"):
        parts.append("ouvre les combats")
    if role == "TOP":
        if lane is not None and lane.leader == "them":
            parts.append("tiens le top sans mourir")
        elif lane is not None and lane.leader == "us":
            parts.append("gagne le top puis aide aux objectifs")
        else:
            parts.append("tiens le top")
        if me.teleport:
            parts.append("TP sur les dragons")
    elif role == "JUNGLE":
        parts.append("joue autour de la voie la plus forte et des dragons")
    elif role == "MIDDLE":
        parts.append("pousse ta vague puis aide aux objectifs")
    elif role == "BOTTOM":
        if us.carry == me.name:
            parts.append(f"tu es le carry, reste derrière {_names(us.frontline, 1) or 'ton équipe'}")
        else:
            parts.append("farme et suis les combats de ton équipe")
    elif role == "UTILITY":
        adc = next((u.name for u in allies if u.role == "BOTTOM"), None)
        if not (me.has("engage") or me.has("pick")):
            parts.append(f"protège {adc or 'ton tireur'}")
        elif not parts:
            parts.append("attrape les ennemis isolés")
    if me.curve == "late" and team.leader != "us":
        parts.append("tu deviens fort plus tard" if any("sans mourir" in x for x in parts)
                     else "ne meurs pas, tu deviens fort plus tard")
    elif lane is not None and lane.leader == "them" and role != "TOP":
        parts.append("ne meurs pas en voie")
    parts = list(dict.fromkeys(parts))[:3]
    return ("Toi : " + ", ".join(parts)) if parts else ""


# ----------------------------------------------------------------------------- reading
@dataclass(frozen=True)
class Reading:
    gt: float
    my_team: str
    my_role: str
    me: str                                   # my champion name
    us: Comp
    them: Comp
    team: Window
    lane: Window | None
    jungle: Window | None
    lanes: dict[str, str]                     # "top" / "mid" / "bot" -> leader now
    ours: tuple[str, ...]                     # our win conditions
    theirs: tuple[str, ...]                   # theirs
    role: str                                 # "Toi : ..."
    threats: tuple[Threat, ...]
    enemy_items: dict[str, list[str]]         # effect (French) -> enemy names
    curve: tuple[tuple[int, float], ...]      # team power projection (minute, edge)
    lane_opp: str | None = None               # alias
    lane_opp_name: str | None = None
    buy: tuple[str, ...] = ()                 # itemization hints from their comp ("résistance magique en priorité")

    # ------------------------------------------------------------------ consumers
    def gauge_factors(self) -> list[tuple[float, str]]:
        """Play-gauge reasons from the power CURVES only (levels / gold gaps are already counted
        by coach.stance_factors): my lane during the laning phase, the team after. Small weights."""
        out: list[tuple[float, str]] = []
        try:
            if self.lane is not None and self.gt < LANING_END_GT and self.my_role != "JUNGLE":
                w = max(-0.6, min(0.6, self.lane.curve_edge * 5.0))
                who = self.lane.who or "ton adversaire"
                # the curve only counts when the whole lane read agrees (a curve "window" with two
                # levels behind is no window: the gap itself is in coach.stance_factors)
                if w >= 0.3 and self.lane.edge >= EVEN:
                    out.append((round(w, 2), f"{who} est faible à ce stade de la partie"))
                elif w <= -0.3 and self.lane.edge <= -EVEN:
                    out.append((round(w, 2), f"{who} est plus fort à ce stade de la partie"))
            elif self.gt >= LANING_END_GT:
                w = max(-0.6, min(0.6, self.team.curve_edge * 6.0))
                if w >= 0.3 and self.team.edge >= EVEN:
                    out.append((round(w, 2), "ton équipe est plus forte à ce stade"))
                elif w <= -0.3 and self.team.edge <= -EVEN:
                    out.append((round(w, 2), "leur équipe est plus forte à ce stade"))
        except Exception:
            log.debug("mastermind gauge failed", exc_info=True)
        return out

    def team_plan(self) -> tuple[str, ...]:
        """Game-start team plan card lines: our win conditions (verb first, the tempo one first)
        + my role."""
        return tuple(x for x in (*self.ours[:2], self.role) if x)

    def snapshot(self) -> dict[str, Any]:
        """Structured dict for the AI advisor (compact French keys)."""
        try:
            return {
                "t": clock(self.gt),
                "nous": self.us.to_dict(), "eux": self.them.to_dict(),
                "fenetre": {"equipe": self.team.to_dict(),
                            "voie": self.lane.to_dict() if self.lane else None,
                            "jungle": self.jungle.to_dict() if self.jungle else None,
                            "voies": dict(self.lanes)},
                "plan_nous": list(self.ours), "plan_eux": list(self.theirs), "mon_role": self.role,
                "menaces": [t.to_dict() for t in self.threats[:3]],
                "objets_ennemis": {k: v[:3] for k, v in self.enemy_items.items()},
                "achats": list(self.buy),
            }
        except Exception:
            log.debug("mastermind snapshot failed", exc_info=True)
            return {}

    def lines(self) -> list[str]:
        """Human-readable summary (debug / UI / tests)."""
        out = [f"Nous : {self.us.summary()}", f"Eux : {self.them.summary()}", f"Équipe : {self.team.text}"]
        if self.lane is not None:
            out.append(f"Voie : {self.lane.text}")
        if self.jungle is not None:
            out.append(f"Jungle : {self.jungle.text}")
        out += [f"Plan : {x}" for x in self.ours] + [f"Leur plan : {x}" for x in self.theirs]
        if self.role:
            out.append(self.role)
        out += [f"Menace : {t.line()}" + (f" [{', '.join(t.tags)}]" if t.tags else "") for t in self.threats[:3]]
        out += [f"Achat : {x}" for x in self.buy]
        if self.enemy_items:
            out.append("Objets ennemis : " + " ; ".join(f"{k} ({', '.join(v)})" for k, v in self.enemy_items.items()))
        return out


def buy_hints(them: list[Unit], me: Unit | None) -> tuple[str, ...]:
    """What their comp asks of MY build (damage type, healing): 2 lines at most. Pure."""
    out: list[str] = []
    if not them:
        return ()
    ap = [u.name for u in them if _split(u)[1] >= 0.6 and int(getattr(u.meta, "ratings", (2,))[0]) >= 2]
    ad = [u.name for u in them if _split(u)[0] >= 0.6 and int(getattr(u.meta, "ratings", (2,))[0]) >= 2]
    if len(ap) >= 3 and len(ap) > len(ad):
        out.append(f"Résistance magique en priorité : {len(ap)} ennemis font des dégâts magiques ({_names(ap, 3)})")
    elif len(ad) >= 3 and len(ad) > len(ap):
        out.append(f"Armure en priorité : {len(ad)} ennemis font des dégâts physiques ({_names(ad, 3)})")
    heal = [u.name for u in them if int(getattr(u.meta, "sustain", 1)) >= 3]
    if heal and (me is None or me.role != "UTILITY"):
        out.append(f"Anti-soin tôt : {_names(heal)} se soigne{'nt' if len(heal) > 1 else ''} beaucoup")
    return tuple(out[:2])


def _facing(me: Unit | None, them: list[Unit], role: str, lane_opp: str | None) -> list[Unit]:
    if lane_opp:
        x = [u for u in them if u.alias.lower() == str(lane_opp).lower()]
        if x:
            if role in ("BOTTOM", "UTILITY"):
                return x + [u for u in them if u.role in ("BOTTOM", "UTILITY") and u not in x]
            return x
    if role in ("BOTTOM", "UTILITY"):
        same = [u for u in them if u.role == role]
        return same + [u for u in them if u.role in ("BOTTOM", "UTILITY") and u not in same]
    return [u for u in them if u.role == role]


def _lane_units(units: list[Unit], lane: str) -> list[Unit]:
    if lane == "bot":
        return [u for u in units if u.role in ("BOTTOM", "UTILITY")]
    role = {"top": "TOP", "mid": "MIDDLE", "jungle": "JUNGLE"}[lane]
    return [u for u in units if u.role == role]


def read_units(us: list[Unit], them: list[Unit], gt: float, *, role: str | None = None, lane_opp: str | None = None,
               game: Any = None) -> Reading | None:
    """A :class:`Reading` from the two unit lists (see :func:`analyze`). Pure, None without data."""
    if not us or not them:
        return None
    me = next((u for u in us if u.is_me), None)
    my_role = str(role or (me.role if me else "") or "").upper()
    cu, ct = comp_of(us, "us"), comp_of(them, "them")
    proj = project(us, them, gt)
    team = window_of("team", us, them, gt, curve=proj)
    lane = jungle = None
    opp_name = opp_alias = None
    if me is not None and my_role and my_role != "JUNGLE":
        facing = _facing(me, them, my_role, lane_opp)
        if facing:
            mine = [me] + ([u for u in us if u.role in ("BOTTOM", "UTILITY") and u is not me]
                           if my_role in ("BOTTOM", "UTILITY") else [])
            opp_alias, opp_name = facing[0].alias, facing[0].name
            lane = window_of("lane", mine[:len(facing)] or [me], facing, gt, who=facing[0].name, mine=me.name)
    jg_us = [u for u in us if u.role == "JUNGLE"]
    jg_them = [u for u in them if u.role == "JUNGLE"]
    if jg_us and jg_them:
        jungle = window_of("jungle", jg_us[:1], jg_them[:1], gt, who=jg_them[0].name, mine=jg_us[0].name)
        if my_role == "JUNGLE":
            lane, opp_alias, opp_name = jungle, jg_them[0].alias, jg_them[0].name
    lanes: dict[str, str] = {}
    for ln in ("top", "mid", "bot"):
        a, b = _lane_units(us, ln), _lane_units(them, ln)
        if a and b:
            lanes[ln] = _lead(edge_at(a, b, gt / 60.0))
    ours, theirs = win_conditions(cu, ct, team)
    rl = role_line(me, cu, ct, team, lane if my_role != "JUNGLE" else None, us) if me is not None else ""
    thr = tuple(threats_of(them, me, game, gt, opp_alias))
    items: dict[str, list[str]] = {}
    for u in them:
        for e, _n in u.effects:
            if e in KEY_EFFECTS:
                items.setdefault(EFFECT_FR[e], []).append(u.name)
    return Reading(buy=buy_hints(them, me), gt=float(gt), my_team=(me.team if me else us[0].team), my_role=my_role, me=me.name if me else "",
                   us=cu, them=ct, team=team, lane=lane, jungle=jungle, lanes=lanes, ours=ours, theirs=theirs,
                   role=rl, threats=thr, enemy_items=items,
                   curve=tuple((m, round(e, 4)) for m, e in proj), lane_opp=opp_alias, lane_opp_name=opp_name)


_cache_lock = threading.Lock()
_cache: tuple[Any, Reading | None] | None = None


def analyze(game: Any, *, role: str | None = None, lane_opp: str | None = None) -> Reading | None:
    """The mastermind :class:`Reading` of a Live Client snapshot (None when spectating / without
    data). Pure, cached on (snapshot, game time, role, lane opponent). Never raises."""
    global _cache
    try:
        if game is None or getattr(game, "me", None) is None:
            return None
        gt = _f(getattr(game, "game_time", 0.0))
        key = (id(game), gt, role, lane_opp, getattr(game, "fetched_at", None))
        with _cache_lock:
            if _cache is not None and _cache[0] == key:
                return _cache[1]
        us, them = units_of(game)
        r = read_units(us, them, gt, role=role, lane_opp=lane_opp, game=game)
        with _cache_lock:
            _cache = (key, r)
        return r
    except Exception:
        log.debug("mastermind.analyze failed", exc_info=True)
        return None


# ----------------------------------------------------------------------------- live model
@dataclass
class _Hist:
    gt: float
    scope: str
    leader: str
    until: float | None
    text: str


class MastermindModel:
    """The engine's live game model (one per game): latest :class:`Reading`, window history (for
    the post-game report), team edge per minute, spoken-window budget. Thread-safe, never raises."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.reading: Reading | None = None
            self.history: list[_Hist] = []
            self.edges: list[tuple[float, float]] = []       # (gt, team edge), one per game minute
            self.start: Reading | None = None                # the first reading (game-start plan)
            self._last_gt: float | None = None
            self._keys: dict[str, str] = {}
            self._voice_gt: float | None = None
            self.calls: list[tuple[float, str]] = []         # (gt, card line) window calls shown
            self.plan_shown = False                          # the game-start team plan banner was offered
            self.first_gt: float | None = None               # first reading of this game (app started mid-game?)
            self._leaders: dict[str, tuple[str, float]] = {}  # scope -> (leader, since gt)

    def update(self, gt: float, game: Any, *, role: str | None = None, lane_opp: str | None = None) -> Reading | None:
        """One tick (the engine's coaching rate, ~1 Hz). Never raises."""
        try:
            gt = float(gt)
            with self._lock:
                if self._last_gt is not None and gt < self._last_gt - 5.0:
                    self._reset_locked()
                self._last_gt = gt
            r = analyze(game, role=role, lane_opp=lane_opp)
            if r is None:
                return None
            with self._lock:
                self.reading = r
                if self.start is None and gt >= 15.0:
                    self.start = r
                if not self.edges or gt - self.edges[-1][0] >= 60.0:
                    self.edges.append((gt, r.team.edge))
                    del self.edges[:-200]
                if self.first_gt is None:
                    self.first_gt = gt
                for w in (r.team, r.lane, r.jungle):
                    if w is None:
                        continue
                    if self._leaders.get(w.scope, (None,))[0] != w.leader:
                        self._leaders[w.scope] = (w.leader, gt)
                    k = f"{w.leader}:{int(w.until // 120) if w.until else 'x'}"
                    if self._keys.get(w.scope) != k:
                        self._keys[w.scope] = k
                        self.history.append(_Hist(gt, w.scope, w.leader, w.until, w.text))
                        del self.history[:-120]
            return r
        except Exception:
            log.debug("MastermindModel.update failed", exc_info=True)
            return None

    def _reset_locked(self) -> None:
        self.reading, self.history, self.edges, self.start = None, [], [], None
        self._keys, self._voice_gt, self.calls, self.plan_shown = {}, None, [], False
        self.first_gt, self._leaders = None, {}

    # ------------------------------------------------------------------ consumers
    def current(self) -> Reading | None:
        with self._lock:
            return self.reading

    @property
    def last_gt(self) -> float | None:
        return self._last_gt

    def gauge_factors(self) -> list[tuple[float, str]]:
        r = self.current()
        return r.gauge_factors() if r is not None else []

    def snapshot(self) -> dict[str, Any] | None:
        r = self.current()
        return r.snapshot() if r is not None else None

    def since(self, scope: str) -> tuple[str | None, float | None]:
        """``(leader, game time it started)`` of a scope's current window (None before data)."""
        with self._lock:
            v = self._leaders.get(scope)
            return (v[0], v[1]) if v else (None, None)

    def watched(self, gt: float) -> float:
        """Seconds of game this model has been reading (a window is only "new" after a while)."""
        with self._lock:
            return 0.0 if self.first_gt is None else max(0.0, float(gt) - self.first_gt)

    def voice_ok(self, gt: float) -> bool:
        """A window call may be spoken now (at most one every :data:`VOICE_GAP_S`)."""
        with self._lock:
            return self._voice_gt is None or not (0.0 <= gt - self._voice_gt < VOICE_GAP_S)

    def note_voice(self, gt: float) -> None:
        with self._lock:
            self._voice_gt = float(gt)

    def note_call(self, gt: float, text: str) -> None:
        with self._lock:
            self.calls.append((float(gt), str(text)))
            del self.calls[:-50]

    def summary(self) -> dict[str, Any]:
        """JSON block for the game record (``record["mastermind"]``): plan at start, final read,
        windows history, team edge per minute, window calls shown."""
        with self._lock:
            r, s = self.reading, self.start
            return {
                "schema": 1,
                "start": s.snapshot() if s is not None else None,
                "final": r.snapshot() if r is not None else None,
                "windows": [[round(h.gt, 1), h.scope, h.leader, round(h.until, 1) if h.until else None, h.text]
                            for h in self.history],
                "edges": [[round(g, 1), round(e, 4)] for g, e in self.edges],
                "calls": [[round(g, 1), x] for g, x in self.calls],
            }


_active: Any = None
_active_lock = threading.Lock()


def set_active(model: MastermindModel | None) -> None:
    """Register the engine's live model (weakly) for the game-changer rule."""
    global _active
    with _active_lock:
        _active = weakref.ref(model) if model is not None else None


def for_ctx(ctx: Any) -> MastermindModel | None:
    """The live model when it describes the planner context's game (same game time ±10 s)."""
    try:
        with _active_lock:
            ref = _active
        m = ref() if ref is not None else None
        if m is None or m.last_gt is None:
            return None
        if abs(float(m.last_gt) - float(getattr(ctx, "gt", 0.0))) > CACHE_KEEP_S:
            return None
        return m
    except Exception:
        return None


def reading_for_ctx(ctx: Any) -> Reading | None:
    """The live model's reading, else a fresh pure read of ``ctx.game``."""
    m = for_ctx(ctx)
    r = m.current() if m is not None else None
    if r is None:
        r = analyze(getattr(ctx, "game", None), role=getattr(ctx, "role", None),
                    lane_opp=(tuple(getattr(ctx, "lane_opps", ()) or ()) or (None,))[0])
    return r


# ----------------------------------------------------------------------------- post-game
class _P:
    """Light PlayerInfo from a record roster row + the final Tab line."""

    def __init__(self, row: dict, line: dict | None) -> None:
        self.champion_alias = str(row.get("alias") or "")
        self.champion_name = str(row.get("name") or self.champion_alias)
        self.team = str(row.get("team") or "")
        self.position = str(row.get("position") or "")
        line = line or {}
        self.level = int(_f(line.get("level"), 1) or 1)
        self.items: list[int] = []
        self.scores = {"kills": line.get("kills", 0), "deaths": line.get("deaths", 0),
                       "assists": line.get("assists", 0)}
        self.is_dead = False
        self.spells: tuple = ()
        self.spell_ids: tuple = ()
        self.item_gold = int(_f(line.get("item_gold"), 0))


def _record_units(record: dict, final: bool) -> tuple[list[Unit], list[Unit], str]:
    roster = [r for r in record.get("roster") or [] if isinstance(r, dict)]
    me_row = next((r for r in roster if r.get("is_me")), None)
    my_team = str((me_row or {}).get("team") or (record.get("meta") or {}).get("team") or "")
    lines = {}
    if final:
        sb = (record.get("scoreboard") or {}).get("final") or {}
        lines = {str(p.get("alias")): p for p in sb.get("players") or [] if isinstance(p, dict)}
    us: list[Unit] = []
    them: list[Unit] = []
    for row in roster:
        p = _P(row, lines.get(str(row.get("alias"))))
        side = "us" if p.team == my_team else "them"
        u = _unit(p, side, bool(row.get("is_me")))
        if u is None:
            continue
        if p.item_gold:
            from dataclasses import replace

            u = replace(u, item_gold=p.item_gold, legendaries=max(u.legendaries, p.item_gold // 3000))
        (us if side == "us" else them).append(u)
    us.sort(key=lambda u: not u.is_me)
    return us, them, my_team


def _team_of(record: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in record.get("roster") or []:
        if not isinstance(r, dict):
            continue
        for k in ("riot_id", "summoner_name"):
            v = str(r.get(k) or "").strip()
            if v:
                out[v.casefold()] = str(r.get("team") or "")
                out[v.split("#", 1)[0].casefold()] = str(r.get("team") or "")
        a = str(r.get("alias") or "")
        if a:
            out.setdefault(a.casefold(), str(r.get("team") or ""))
    return out


def _obj_events(record: dict, my_team: str) -> list[tuple[float, str, str]]:
    """``(gt, what, "us" | "them")`` of the epic objectives and towers of a record."""
    look = _team_of(record)
    out = []
    for ev in record.get("events") or []:
        if not isinstance(ev, dict):
            continue
        name = ev.get("EventName")
        gt = _f(ev.get("EventTime"))
        what = {"DragonKill": "dragon", "BaronKill": "Baron", "HeraldKill": "Héraut", "HordeKill": "larves",
                "TurretKilled": "tour", "InhibKilled": "inhibiteur"}.get(str(name))
        if what is None:
            continue
        team = None
        if what in ("tour", "inhibiteur"):
            s = str(ev.get("TurretKilled") or ev.get("InhibKilled") or "")
            owner = "ORDER" if "_T1" in s else "CHAOS" if "_T2" in s else None
            if owner:
                team = "them" if owner == my_team else "us"
        if team is None:
            killer = str(ev.get("KillerName") or "").casefold()
            t = look.get(killer) or look.get(killer.split("#", 1)[0])
            if t:
                team = "us" if t == my_team else "them"
        if team is None:
            continue
        if what == "dragon" and str(ev.get("DragonType") or "").casefold() == "elder":
            what = "ancestral"
        out.append((gt, what, team))
    out.sort()
    merged: list[tuple[float, str, str]] = []
    for row in out:                               # the 3 grubs of one take are one objective
        if row[1] == "larves" and any(m[1] == "larves" and m[2] == row[2] and 0.0 <= row[0] - m[0] <= 90.0
                                      for m in merged[-3:]):
            continue
        merged.append(row)
    return merged


def _deaths_of_me(record: dict) -> list[float]:
    roster = [r for r in record.get("roster") or [] if isinstance(r, dict)]
    me = next((r for r in roster if r.get("is_me")), None)
    if me is None:
        return []
    names = {str(me.get(k) or "").casefold() for k in ("riot_id", "summoner_name")} - {""}
    names |= {n.split("#", 1)[0] for n in names}
    return [_f(ev.get("EventTime")) for ev in record.get("events") or []
            if isinstance(ev, dict) and ev.get("EventName") == "ChampionKill"
            and str(ev.get("VictimName") or "").casefold() in names]


def understanding(record: Any) -> dict[str, Any] | None:
    """The post-game "Compréhension de la partie": both comps, the plan at the start, the power
    windows over the game (curves + the Tab gold timeline), what the team did vs the plan
    (objectives taken during each window), my role vs what happened. None without a roster.
    Uses the live block ``record["mastermind"]`` when present. Never raises."""
    try:
        if not isinstance(record, dict):
            return None
        us0, them0, my_team = _record_units(record, final=False)
        if len(us0) < 2 or len(them0) < 2:
            return None
        start = read_units(us0, them0, 90.0, role=None)
        usf, themf, _ = _record_units(record, final=True)
        dur = _f(record.get("duration"), 0.0) or _f((record.get("summary") or {}).get("duration"), 0.0)
        final = read_units(usf, themf, dur or 1800.0, role=None)
        if start is None or final is None:
            return None
        live = record.get("mastermind") if isinstance(record.get("mastermind"), dict) else {}
        # -- team edge per minute: live edges, else curve + the Tab gold timeline
        tl = (record.get("scoreboard") or {}).get("timeline") or []
        edges: list[tuple[float, float]] = []
        if live.get("edges"):
            edges = [(float(g), float(e)) for g, e in live["edges"] if isinstance(g, (int, float))]
        elif tl:
            for row in tl:
                try:
                    gt, gold = float(row[0]), float(row[1])
                except (TypeError, ValueError, IndexError):
                    continue
                m = gt / 60.0
                edges.append((gt, _curve_mean(us0, m) - _curve_mean(them0, m) + gold / 5.0 / expected_resource(m)))
        else:
            for m in range(0, int((dur or 1800.0) // 60) + 1):
                edges.append((m * 60.0, _curve_mean(us0, m) - _curve_mean(them0, m)))
        segs: list[list[Any]] = []                  # [t0, t1, leader]
        for gt, e in edges:
            lv = _lead(e)
            if segs and segs[-1][2] == lv:
                segs[-1][1] = gt
            else:
                if segs:
                    segs[-1][1] = gt
                segs.append([gt if segs else 0.0, gt, lv])
        if segs:
            segs[-1][1] = max(segs[-1][1], dur or segs[-1][1])
        objs = _obj_events(record, my_team)
        did: list[str] = []
        for t0, t1, lv in segs:
            if t1 - t0 < 120.0 or lv == "even":
                continue
            got = [w for g, w, side in objs if t0 <= g < t1 and side == "us" and w != "tour"]
            lost = [w for g, w, side in objs if t0 <= g < t1 and side == "them" and w != "tour"]
            tw_us = sum(1 for g, w, side in objs if t0 <= g < t1 and side == "us" and w == "tour")
            tw_them = sum(1 for g, w, side in objs if t0 <= g < t1 and side == "them" and w == "tour")
            span = f"{clock(t0)}-{clock(t1)}"
            if lv == "us":
                verdict = "bien joué" if len(got) + tw_us >= max(1, len(lost) + tw_them) else "fenêtre gâchée"
                did.append(f"{span} ton équipe était plus forte : {len(got)} objectif(s) pris, {len(lost)} perdu(s), "
                           f"tours {tw_us}-{tw_them} ({verdict})")
            else:
                verdict = "limité les dégâts" if len(lost) <= max(1, len(got)) else "trop d'objectifs donnés"
                did.append(f"{span} ils étaient plus forts : {len(lost)} objectif(s) perdu(s), {len(got)} pris, "
                           f"tours {tw_us}-{tw_them} ({verdict})")
        deaths = _deaths_of_me(record)
        me = next((u for u in us0 if u.is_me), None)
        role_check = ""
        if me is not None:
            early = sum(1 for d in deaths if d < LANING_END_GT)
            plan_role = start.role or ""
            if start.lane is not None and start.lane.leader == "us" and early >= 2:
                role_check = (f"Ta voie était gagnable tôt ({start.lane.text.lower()}) mais tu es mort {early} fois "
                              f"avant 14:00 : l'avance est passée de l'autre côté.")
            elif early >= 3:
                role_check = f"Plan : ne pas mourir en voie. Réalité : {early} morts avant 14:00."
            elif deaths:
                role_check = f"{len(deaths)} mort(s) dans la partie, dont {early} avant 14:00."
            if plan_role:
                role_check = (plan_role + ". " + role_check).strip()
        out = {
            "nous": start.us.to_dict(), "eux": start.them.to_dict(),
            "plan": {"fenetre": start.team.text, "voie": start.lane.text if start.lane else None,
                     "jungle": start.jungle.text if start.jungle else None,
                     "nous": list(start.ours), "eux": list(start.theirs), "mon_role": start.role},
            "fenetres": [[round(a, 1), round(b, 1), lv] for a, b, lv in segs],
            "fait": did,
            "objectifs": [[round(g, 1), w, side] for g, w, side in objs if w != "tour"],
            "role": role_check,
            "menaces": [t.to_dict() for t in final.threats[:3]],
            "fin": {"fenetre": final.team.text},
            "live": bool(live),
        }
        if live.get("calls"):
            out["appels"] = live["calls"][-10:]
        return out
    except Exception:
        log.debug("mastermind.understanding failed", exc_info=True)
        return None


def attach_to_record(record_path: Any, summary: dict[str, Any]) -> bool:
    """Write the live model block into the game record as ``record["mastermind"]`` (atomic)."""
    try:
        import json
        import os
        from pathlib import Path

        p = Path(record_path)
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False
        data["mastermind"] = summary
        tmp = p.with_name(p.name + f".{os.getpid()}.mm.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, p)
        return True
    except Exception:
        log.exception("Cannot add the mastermind block to %s", record_path)
        return False


__all__ = ["Unit", "Comp", "Window", "Threat", "Reading", "MastermindModel", "analyze", "read_units", "units_of",
           "comp_of", "window_of", "project", "edge_at", "curve_value", "threats_of", "win_conditions",
           "role_line", "set_active", "for_ctx", "reading_for_ctx", "understanding", "attach_to_record", "clock"]
