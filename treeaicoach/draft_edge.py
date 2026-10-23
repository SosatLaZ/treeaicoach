"""DRAFT EDGE: what a coach reads from the ten picks before the first minion (champion select /
loading screen / game start), in plain French, one short line per idea.

Built ON the existing reads, never beside them:

* the compositions and the power curves are :mod:`treeaicoach.mastermind` (``Comp``, ``Window``,
  ``edge_at``) fed with level-1, item-less units (the draft only, no live data);
* the counter items are :mod:`treeaicoach.itemization` (``enemy_profile``, ``NEED_ITEMS``,
  ``champion_class``); the rune names come from the live rune data (:mod:`treeaicoach.meta_builds`);
* the enemy jungler forecast is the validated red-side rule of :mod:`treeaicoach.first_gank`
  (blue start in 15 / 20 real games, first gank on his red side in 6 / 7), refined by the lanes.

The features (all pure, never raise, None / "" when the data is missing):

* :func:`win_condition` - team comp reading -> ONE sentence ("On scale mieux : joue le temps jusqu'à
  25 min, puis Baron"), :func:`comp_detail` - AD / AP split, engage, peel, waveclear, scaling of
  both teams in one line;
* :func:`spike_timeline` - who wins levels 1-3, level 6, first item, two items in my matchup
  ("Tu gagnes niv 1-3, il gagne au 6, toi après 2 objets");
* :func:`counter_tweak` - enemy-comp-aware first buy / rune on top of the meta build ("Contre eux :
  Sandales de Mercure tôt + rune Second souffle (4 dégâts magiques)");
* :func:`lane_edges` / :func:`lanes_line` / :func:`play_around` - lane by lane, who is favoured and
  which lane the team plays around ("Jouez bot : 2v2 gagnant"), also said to the Discord friends;
* :func:`jungle_forecast` - the enemy jungler's likely start side and first-gank lane;
* :func:`parse_history` / :func:`tilt_line` - session guard from MY OWN match history (League
  Client, local, read-only): a calm "2 défaites d'affilée : pause 10 min ?" before the next game.

Privacy (Riot policy + the app's rules): champion picks and MY own results only. No summoner name,
Riot ID or PUUID is read into these structures or written anywhere; no other player's history.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

LANES = ("top", "mid", "bot")
LANE_ROLES = {"top": ("TOP",), "mid": ("MIDDLE",), "bot": ("BOTTOM", "UTILITY"), "jungle": ("JUNGLE",)}
ROLE_LANE = {"TOP": "top", "MIDDLE": "mid", "BOTTOM": "bot", "UTILITY": "bot", "JUNGLE": "jungle"}
CARD_MAX = 60                     # a card / banner line (mastermind.CARD_MAX)
EVEN = 0.035                      # mastermind.EVEN: |edge| below this is even
LANE_EVEN = 0.04                  # lane edge below this: "égal"
SPIKE_EVEN = 0.2                 # spike phase score below this: even
#: (phase label, game minute the phase is read at)
PHASES = ("niv 1-3", "au 6", "au 1er objet", "après 2 objets")
_RANGE_LABEL = {(0, 1): "niv 1-6", (1, 2): "du 6 au 1er objet", (2, 3): "dès le 1er objet", (1, 3): "dès le 6",
                (0, 2): "jusqu'au 1er objet", (0, 3): "à tous les stades"}


def _f(x: Any, d: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return d
    return v if math.isfinite(v) else d


def _meta(alias: str) -> Any:
    from treeaicoach import mastermind

    return mastermind._profile(alias)


def _pick(x: Any) -> tuple[str, str, str]:
    """(alias, name, ROLE) of a champion select Pick, a Live Client PlayerInfo or a tuple."""
    if isinstance(x, (tuple, list)):
        a = str(x[0] if len(x) > 0 else "")
        return a, str(x[1] if len(x) > 1 and x[1] else a), str(x[2] if len(x) > 2 else "").upper()
    a = str(getattr(x, "alias", "") or getattr(x, "champion_alias", "") or "")
    n = str(getattr(x, "name", "") or getattr(x, "champion_name", "") or "") or str(getattr(_meta(a), "name", "") or a)
    return a, n, str(getattr(x, "position", "") or "").upper()


def units(picks: Iterable[Any], side: str, me_alias: str = "") -> list[Any]:
    """Level-1, item-less :class:`treeaicoach.mastermind.Unit` of draft picks (the draft only)."""
    from treeaicoach import mastermind

    out = []
    for p in picks or ():
        a, n, r = _pick(p)
        if not a:
            continue
        u = mastermind._unit(SimpleNamespace(champion_alias=a, champion_name=n, position=r, level=1, items=[],
                                             scores={}), side, is_me=bool(me_alias) and a == me_alias)
        if u is not None:
            out.append(u)
    return out


# ----------------------------------------------------------------------------- 8. team comp reading
def _edges(us: list[Any], them: list[Any]) -> tuple[float, float, float | None]:
    """(edge at 10 min, edge at 30 min, minute the team lead flips: the mastermind team window) -
    curves only (level-1 units: no resource gap)."""
    from treeaicoach import mastermind as mm

    e10 = mm.edge_at(us, them, 10.0, res_gap=0.0)
    e30 = mm.edge_at(us, them, 30.0, res_gap=0.0)
    w = mm.window_of("team", us, them, 0.0)
    return e10, e30, (w.until / 60.0 if w.until else None)


def _late_plan(us: Any, them: Any) -> str:
    """The mid / late plan from the two comps (engage, peel, waveclear, damage)."""
    se, te = us.scores.get("engage", 0.0), them.scores.get("engage", 0.0)
    if se >= te + 1.0 and se >= 1.5:
        return "engage sur le Baron"
    if us.waveclear >= them.waveclear + 0.4:
        return "assiège les tours"
    if te >= se + 1.0 and us.peel < 1.5:
        return "groupe serré, pas de face-check"
    if us.scores.get("split", 0.0) >= 2.0 and us.scores.get("split", 0.0) > them.scores.get("split", 0.0):
        return "split et Baron"
    return "puis Baron"


def win_condition(us: Any, them: Any, us_units: list[Any] | None = None, them_units: list[Any] | None = None
                  ) -> str:
    """ONE French win-condition sentence (<= :data:`CARD_MAX` characters, "why : what", the plan in
    tutoiement so :func:`treeaicoach.presenter.card_line` turns it into card material). ``us`` /
    ``them``: :class:`mastermind.Comp`; the units give the power curves. Pure, never raises."""
    try:
        if us is None or them is None or not us_units or not them_units:
            return ""
        return _sentence(us, them, *_edges(us_units, them_units))
    except Exception:
        log.debug("win_condition failed", exc_info=True)
        return ""


def reading_win_condition(reading: Any) -> str:
    """:func:`win_condition` of a live :class:`treeaicoach.mastermind.Reading` (game start): its
    team projection (minute, edge) gives the curves. "" without data. Never raises."""
    try:
        curve = dict((int(m), float(e)) for m, e in (getattr(reading, "curve", None) or ()))
        if reading is None or 10 not in curve or 30 not in curve:
            return ""
        until = getattr(getattr(reading, "team", None), "until", None)
        flip = float(until) / 60.0 if until else None
        return _sentence(reading.us, reading.them, curve[10], curve[30], flip)
    except Exception:
        log.debug("reading_win_condition failed", exc_info=True)
        return ""


def _sentence(us: Any, them: Any, e10: float, e30: float, flip: float | None) -> str:
    try:
        m = int(flip) if flip else 25
        plan = _late_plan(us, them)
        if e10 <= -EVEN and e30 >= EVEN:
            s = f"On scale mieux : joue le temps jusqu'à {m} min, {plan}"
        elif e10 >= EVEN and e30 <= -EVEN:
            s = f"On est plus forts tôt : force dragons et Héraut avant {m} min"
        elif e10 >= EVEN and e30 >= EVEN:
            s = "On est plus forts : force les objectifs, " + ("engage" if "engage" in plan else plan.replace("puis ", ""))
        elif e10 <= -EVEN and e30 <= -EVEN:
            s = "Ils sont plus forts sur le papier : joue les erreurs, défends"
        elif e30 - e10 >= 0.03:
            s = f"On scale un peu mieux : évite les combats tôt, {plan}"
        elif e10 - e30 >= 0.03:
            s = "On est un peu plus forts tôt : force les dragons"
        else:
            s = f"Draft équilibrée : {_even_plan(us, them)}"
        return s if len(s) <= CARD_MAX else s.split(", ")[0]
    except Exception:
        log.debug("win_condition failed", exc_info=True)
        return ""


def _even_plan(us: Any, them: Any) -> str:
    ident = us.identity[0] if us.identity else "teamfight"
    return {"engage": "engage à 5 sur les objectifs", "poke": "harcèle avant chaque objectif",
            "pick": "attrape les isolés avec vision", "dive": f"plonge sur {them.carry or 'leur carry'}",
            "split": "pousse les voies de côté", "siege": "assiège les tours"}.get(
        ident, f"groupe autour de {us.carry or 'ton carry'}")


def _dmg(c: Any) -> str:
    if c.ap >= c.ad:
        return f"{int(round(100 * c.ap))} % AP"
    return f"{int(round(100 * c.ad))} % AD"


def comp_detail(us: Any, them: Any, us_units: list[Any] | None = None, them_units: list[Any] | None = None) -> str:
    """"Nous 70 % AD, eux 75 % AP · plus d'engage, moins de peel, on scale" (both teams' AD / AP,
    then what WE have more / less of: engage, peel, waveclear, scaling). Pure, never raises."""
    try:
        if us is None or them is None:
            return ""
        out = f"Nous {_dmg(us)}, eux {_dmg(them)}"
        diffs: list[str] = []
        se, te = us.scores.get("engage", 0.0), them.scores.get("engage", 0.0)
        if se >= te + 1.0:
            diffs.append("plus d'engage")
        elif te >= se + 1.0:
            diffs.append("moins d'engage")
        if us.peel >= them.peel + 1.0:
            diffs.append("plus de peel")
        elif them.peel >= us.peel + 1.0:
            diffs.append("moins de peel")
        if us.waveclear >= them.waveclear + 0.4:
            diffs.append("meilleur nettoyage")
        elif them.waveclear >= us.waveclear + 0.4:
            diffs.append("moins bon nettoyage")
        if us_units and them_units:
            e10, e30, _fl = _edges(us_units, them_units)
            if e30 - e10 >= 0.03:
                diffs.append("on scale")
            elif e10 - e30 >= 0.03:
                diffs.append("plus forts tôt")
        return out + (" · " + ", ".join(diffs[:3]) if diffs else "")
    except Exception:
        log.debug("comp_detail failed", exc_info=True)
        return ""


@dataclass(frozen=True)
class CompRead:
    sentence: str                 # the win condition ("On scale mieux : ...")
    detail: str                   # AD / AP, engage, peel, waveclear, scaling
    us: Any = None                # mastermind.Comp
    them: Any = None


def comp_read(allies: Iterable[Any], enemies: Iterable[Any], me_alias: str = "") -> CompRead | None:
    """The team comp reading of a draft (``allies`` me included). None under 3 picks a side."""
    try:
        from treeaicoach import mastermind as mm

        us, them = units(allies, "us", me_alias), units(enemies, "them")
        if len(us) < 3 or len(them) < 3:
            return None
        cu, ct = mm.comp_of(us, "us"), mm.comp_of(them, "them")
        return CompRead(win_condition(cu, ct, us, them), comp_detail(cu, ct, us, them), cu, ct)
    except Exception:
        log.debug("comp_read failed", exc_info=True)
        return None


# ----------------------------------------------------------------------------- 9. power spikes
def phase_scores(me_alias: str, opp_alias: str) -> tuple[float, float, float, float]:
    """My edge over my lane opponent per phase (levels 1-3, level 6, 1 item, 2 items), > 0 mine."""
    from treeaicoach import mastermind as mm

    me, op = _meta(me_alias), _meta(opp_alias)
    cm, co = str(getattr(me, "curve", "mid") or "mid"), str(getattr(op, "curve", "mid") or "mid")

    def cv(m: float) -> float:
        return (mm.curve_value(cm, m) - mm.curve_value(co, m)) * 5.0

    dmg = int(getattr(me, "ratings", (2,))[0]) - int(getattr(op, "ratings", (2,))[0])
    rng = (0.15 if me.ranged and not op.ranged else -0.15 if op.ranged and not me.ranged else 0.0)
    l13 = cv(3.0) + 0.1 * dmg + rng
    l6 = 0.6 * (int(getattr(me, "spike6", 2)) - int(getattr(op, "spike6", 2))) + 0.5 * cv(7.0)
    i1 = cv(14.0) + 0.1 * dmg
    i2 = cv(24.0) + 0.1 * dmg
    return (round(l13, 3), round(l6, 3), round(i1, 3), round(i2, 3))


def _who(s: float) -> str:
    return "me" if s >= SPIKE_EVEN else "opp" if s <= -SPIKE_EVEN else "even"


def spike_timeline(me_alias: str, opp_alias: str) -> str:
    """"Tu gagnes niv 1-3, il gagne au 6, toi après 2 objets" (consecutive phases with the same
    winner merged). "" when a profile is unknown. Pure, never raises."""
    try:
        me, op = _meta(me_alias), _meta(opp_alias)
        if not (getattr(me, "known", False) and getattr(op, "known", False)):
            return ""
        il = "elle" if getattr(op, "female", False) else "il"
        won = [_who(s) for s in phase_scores(me_alias, opp_alias)]
        for i in range(1, len(won) - 1):          # an even phase between two same leads is that lead
            if won[i] == "even" and won[i - 1] == won[i + 1] != "even":
                won[i] = won[i - 1]
        segs: list[tuple[str, int, int]] = []
        for i, w in enumerate(won):
            if segs and segs[-1][0] == w:
                segs[-1] = (w, segs[-1][1], i)
            else:
                segs.append((w, i, i))
        parts = []
        for k, (w, a, b) in enumerate(segs):
            lab = PHASES[a] if a == b else _RANGE_LABEL[(a, b)]
            if w == "me":
                parts.append(f"Tu gagnes {lab}" if k == 0 else f"toi {lab}")
            elif w == "opp":
                parts.append(f"{il.capitalize() if k == 0 else il} gagne {lab}")
            else:
                parts.append(f"{'Égal' if k == 0 else 'égal'} {lab}")
        return ", ".join(parts)
    except Exception:
        log.debug("spike_timeline failed", exc_info=True)
        return ""


# ----------------------------------------------------------------------------- 10. counter tweak
#: the cheap anti-heal component per class (bought early, before the meta first item is done)
ANTIHEAL_EARLY = {"marksman": 3123, "assassin_ad": 3123, "fighter": 3123, "mage": 3916, "assassin_ap": 3916,
                  "fighter_ap": 3916, "tank": 3076, "support_tank": 3076, "enchanter": 3916}
MERCURY, STEELCAPS = 3111, 3047
SECOND_WIND, BONE_PLATING = 8444, 8473
AP_HEAVY, AD_HEAVY = 0.65, 0.70


@dataclass(frozen=True)
class CounterTweak:
    need: str                     # "antiheal" | "magic" | "physical" | "burst"
    item_id: int | None
    item: str                     # French item name ("" when only the rune changes)
    rune_id: int | None
    rune: str
    reason: str                   # "4 dégâts magiques", "Vladimir se soigne"

    @property
    def text(self) -> str:
        what = " + ".join(x for x in (f"{self.item} tôt" if self.item else "",
                                      f"rune {self.rune}" if self.rune else "") if x)
        return f"Contre eux : {what} ({self.reason})" if what else ""


def counter_tweak(my_alias: str, role: str, enemies: Iterable[Any], items: dict[int, Any] | None = None
                  ) -> CounterTweak | None:
    """The one enemy-comp-aware change to the meta build: early anti-heal vs a strong healer, tier-2
    boots + Second Wind vs heavy AP, Plate Steelcaps vs heavy AD, Bone Plating vs 2+ burst
    champions. None when their comp asks nothing special. Pure, never raises."""
    try:
        from treeaicoach import itemization as iz

        foes = [_pick(e) for e in enemies or ()]
        foes = [(a, n) for a, n, _r in foes if a]
        if len(foes) < 2:
            return None
        table = items if items is not None else iz.load_items()
        prof = iz.enemy_profile([SimpleNamespace(champion_alias=a, champion_name=n, level=1, items=[], kills=0,
                                                 deaths=0) for a, n in foes], table)
        cls = iz.champion_class(my_alias, role or None)
        me = _meta(my_alias)
        melee = not bool(getattr(me, "ranged", False))
        support = str(role or "").upper() == "UTILITY"

        def name_of(iid: int | None) -> str:
            it = table.get(iid) if iid is not None else None
            return str(getattr(it, "name", "") or "") if it is not None and getattr(it, "rift", True) else ""

        def rune_name(pid: int) -> str:
            try:
                from treeaicoach import meta_builds

                return meta_builds.names()["perks"].get(str(pid), "")
            except Exception:
                return ""

        heal = prof.names.get("antiheal") or []
        if prof.needs.get("antiheal", 0.0) >= 1.0 and heal and not support:
            iid = ANTIHEAL_EARLY.get(cls)
            verb = "se soignent" if len(heal) > 1 else "se soigne"
            if name_of(iid):
                return CounterTweak("antiheal", iid, name_of(iid), None, "",
                                    f"{' et '.join(heal[:2])} {verb}")
        ap_heavy = prof.magic >= AP_HEAVY
        ad_heavy = prof.physical >= AD_HEAVY
        n_ap = sum(1 for a, _n in foes if iz.damage_split(a)[1] >= 0.6)
        n_ad = sum(1 for a, _n in foes if iz.damage_split(a)[0] >= 0.6)
        if ap_heavy:
            rid = SECOND_WIND if melee else None
            return CounterTweak("magic", MERCURY, name_of(MERCURY), rid, rune_name(rid) if rid else "",
                                f"{n_ap} dégâts magiques")
        if ad_heavy:
            rid = BONE_PLATING if melee else None
            return CounterTweak("physical", STEELCAPS, name_of(STEELCAPS), rid, rune_name(rid) if rid else "",
                                f"{n_ad} dégâts physiques")
        burst = [n for a, n in foes if a in iz.BURST or "ASSASSIN" in (getattr(_meta(a), "classes", ()) or ())]
        if len(burst) >= 2:
            return CounterTweak("burst", None, "", BONE_PLATING, rune_name(BONE_PLATING),
                                f"burst de {' et '.join(burst[:2])}")
        return None
    except Exception:
        log.debug("counter_tweak failed", exc_info=True)
        return None


# ----------------------------------------------------------------------------- 11. lanes
def _lane_power(u: Any) -> float:
    """Laning strength of one unit at ~6 min (curve, level-6 spike, damage, range)."""
    from treeaicoach import mastermind as mm

    m = u.meta
    return (mm.curve_value(u.curve, 6.0) + 0.04 * (int(getattr(m, "spike6", 2)) - 2)
            + 0.03 * (int(getattr(m, "ratings", (2,))[0]) - 2) + (0.02 if u.ranged else 0.0))


def lane_edges(allies: Iterable[Any], enemies: Iterable[Any]) -> dict[str, float]:
    """``{"top": +0.06, "mid": -0.01, "bot": +0.08}``: our laning edge per lane (bot is the 2v2),
    only lanes with a champion on both sides. Pure, never raises."""
    out: dict[str, float] = {}
    try:
        us, them = units(allies, "us"), units(enemies, "them")
        for lane in LANES:
            a = [u for u in us if u.role in LANE_ROLES[lane]]
            b = [u for u in them if u.role in LANE_ROLES[lane]]
            if not a or not b:
                continue
            e = sum(_lane_power(u) for u in a) / len(a) - sum(_lane_power(u) for u in b) / len(b)
            if lane != "bot" and a[0].ranged != b[0].ranged:          # 1v1 range advantage
                e += 0.02 if a[0].ranged else -0.02
            out[lane] = round(e, 4)
    except Exception:
        log.debug("lane_edges failed", exc_info=True)
    return out


def _verdict(e: float) -> str:
    return "favorable" if e >= LANE_EVEN else "difficile" if e <= -LANE_EVEN else "égal"


def lanes_line(edges: dict[str, float]) -> str:
    """"Voies : top favorable, mid égal, bot difficile"."""
    parts = [f"{ln} {_verdict(edges[ln])}" for ln in LANES if ln in edges]
    return ("Voies : " + ", ".join(parts)) if parts else ""


def play_around(edges: dict[str, float], allies: Iterable[Any] = ()) -> str:
    """"Jouez bot : 2v2 gagnant" / "Jouez top : Garen gagne sa voie" / "Jouez les objectifs : aucune
    voie gagnante" - the lane the team (and the jungler) plays around. "" without lanes."""
    if not edges:
        return ""
    lane, e = max(edges.items(), key=lambda kv: kv[1])
    if e < LANE_EVEN:
        if all(v <= -LANE_EVEN for v in edges.values()):
            return "Jouez les objectifs à 5 : aucune voie gagnante"
        return "Voies égales : jouez autour du premier qui gagne"
    if lane == "bot":
        return "Jouez bot : 2v2 gagnant"
    who = next((n for a, n, r in (_pick(p) for p in allies) if r in LANE_ROLES[lane]), "")
    return f"Jouez {lane} : {who} gagne sa voie" if who else f"Jouez {lane} : voie gagnante"


def friend_suffix(role: str, edges: dict[str, float]) -> str:
    """Extra clause for a Discord friend's matchup sentence when the team plays around his lane."""
    lane = ROLE_LANE.get(str(role or "").upper())
    if not edges or lane not in edges:
        return ""
    best, e = max(edges.items(), key=lambda kv: kv[1])
    return " L'équipe joue autour de ta voie." if best == lane and e >= LANE_EVEN else ""


# ----------------------------------------------------------------------------- 12. enemy jungler
@dataclass(frozen=True)
class JungleForecast:
    name: str
    start: str | None             # map half of his (blue buff) start: "top" | "bot" | None (side unknown)
    gank: str                     # "top" | "mid" | "bot": most likely first gank
    when: str                     # "2:45" | "3:15"
    early: bool                   # an early ganker (level-3 gank)

    def text(self, my_role: str = "") -> str:
        """The card line, written for MY role."""
        half = {"top": "en haut", "bot": "en bas"}.get(self.start or "", "")
        lane = ROLE_LANE.get(str(my_role or "").upper())
        if lane == "jungle":
            s = f"Contre-gank {self.gank} vers {self.when} : {self.name} commence {half}" if half \
                else f"Contre-gank {self.gank} vers {self.when} : premier gank de {self.name}"
        elif lane == self.gank:
            s = f"Balise ta rivière avant {self.when} : {self.name} vient {self.gank}"
        else:
            s = f"{self.name} : départ {half}, 1er gank {self.gank} vers {self.when}" if half \
                else f"{self.name} : 1er gank probable {self.gank} vers {self.when}"
        return s if len(s) <= CARD_MAX else s.split(" : ")[0]


def _gankable(u: Any) -> float:
    """How easy a laner is to kill in a gank: low mobility, melee, no escape."""
    m = u.meta
    return 0.15 * (3 - int(getattr(m, "mobility", 2))) + (0.05 if u.ranged else 0.0)


def _threat(u: Any) -> float:
    """How much a laner helps his jungler gank: crowd control, early curve."""
    m = u.meta
    return 0.1 * int(getattr(m, "cc", 1)) + (0.15 if u.curve == "early" else 0.0) + (
        0.1 if u.has("engage") or u.has("pick") else 0.0)


def jungle_forecast(allies: Iterable[Any], enemies: Iterable[Any], my_team: str = "",
                    edges: dict[str, float] | None = None) -> JungleForecast | None:
    """Enemy jungler's likely start half + first-gank lane (see the module doc). Pure, never raises."""
    try:
        from treeaicoach import first_gank

        us, them = units(allies, "us"), units(enemies, "them")
        jg = next((u for u in them if u.role == "JUNGLE"), None)
        if jg is None:
            return None
        red = first_gank.danger_lane(my_team)            # lane next to HIS red buff (end of a blue start)
        start = ({"top": "bot", "bot": "top"}.get(red) if red else None)
        edges = edges if edges is not None else lane_edges(allies, enemies)
        best, best_s = "", -1e9
        for lane in LANES:
            a = [u for u in us if u.role in LANE_ROLES[lane]]
            b = [u for u in them if u.role in LANE_ROLES[lane]]
            if red:
                s = 1.0 if lane == red else 0.5 if lane == "mid" else 0.45
            else:
                s = 0.7 if lane != "mid" else 0.5
            s += max((_gankable(u) for u in a), default=0.0) + max((_threat(u) for u in b), default=0.0)
            if edges.get(lane, 0.0) <= -LANE_EVEN:
                s += 0.2                                  # he plays around his winning lane
            if s > best_s:
                best, best_s = lane, s
        early = jg.curve == "early" or jg.has("dive") or jg.has("engage")
        return JungleForecast(jg.name, start, best, "2:45" if early else "3:15", early)
    except Exception:
        log.debug("jungle_forecast failed", exc_info=True)
        return None


# ----------------------------------------------------------------------------- 13. session / tilt guard
REMAKE_S = 300.0                  # games shorter than this are remakes: ignored
SESSION_GAP_S = 75 * 60.0         # two games further apart than this are not the same session
FRESH_S = 2 * 3600.0              # the last game must have ended within this to talk about "now"
SKIP_GAME_TYPES = frozenset({"CUSTOM_GAME", "TUTORIAL_GAME"})
SKIP_GAME_MODES = frozenset({"PRACTICETOOL", "TUTORIAL", "TUTORIAL_MODULE_1", "TUTORIAL_MODULE_2",
                             "TUTORIAL_MODULE_3"})


@dataclass(frozen=True)
class GameResult:
    """One of MY games (numbers only: no name, no id)."""
    win: bool
    kills: int
    deaths: int
    assists: int
    start_s: float                # epoch seconds
    duration_s: float

    @property
    def kda(self) -> float:
        return (self.kills + self.assists) / max(1, self.deaths)

    @property
    def end_s(self) -> float:
        return self.start_s + self.duration_s


def parse_history(games: Any) -> list[GameResult]:
    """League Client match-history list entries (current summoner) -> my results, newest first.
    Keeps only win / K / D / A / time; remakes are dropped. Pure, never raises."""
    out: list[GameResult] = []
    try:
        for g in games if isinstance(games, list) else []:
            if not isinstance(g, dict):
                continue
            if (str(g.get("gameType") or "").upper() in SKIP_GAME_TYPES
                    or str(g.get("gameMode") or "").upper() in SKIP_GAME_MODES):
                continue                                  # custom / practice tool / tutorial: not a real result
            parts = g.get("participants")
            p = parts[0] if isinstance(parts, list) and parts and isinstance(parts[0], dict) else None
            st = p.get("stats") if p is not None and isinstance(p.get("stats"), dict) else None
            if st is None or not isinstance(st.get("win"), bool):
                continue
            dur = _f(g.get("gameDuration"))
            if dur > 100000:                              # some payloads give milliseconds
                dur /= 1000.0
            if dur < REMAKE_S or st.get("gameEndedInEarlySurrender") is True:
                continue
            out.append(GameResult(bool(st["win"]), int(_f(st.get("kills"))), int(_f(st.get("deaths"))),
                                  int(_f(st.get("assists"))), _f(g.get("gameCreation")) / 1000.0, dur))
    except Exception:
        log.debug("parse_history failed", exc_info=True)
    out.sort(key=lambda r: -r.start_s)
    return out


def session_games(results: Sequence[GameResult], now_s: float) -> list[GameResult]:
    """The games of the current play session (newest first): chained with < 75 min gaps, the last
    one finished within 2 h."""
    if not results or now_s - results[0].end_s > FRESH_S:
        return []
    out = [results[0]]
    for r in results[1:]:
        if out[-1].start_s - r.end_s > SESSION_GAP_S:
            break
        out.append(r)
    return out


def tilt_line(results: Sequence[GameResult], now_s: float) -> str:
    """A calm suggestion before the next game, or "": a losing streak in this session ("2 défaites
    d'affilée : pause 10 min ?"), a falling KDA, or a long session. Pure, never raises."""
    try:
        sess = session_games(results, now_s)
        if not sess:
            return ""
        streak = 0
        for r in sess:
            if r.win:
                break
            streak += 1
        if streak >= 3:
            return f"{streak} défaites d'affilée : vraie pause de 15 min ?"
        if streak == 2:
            return "2 défaites d'affilée : pause 10 min ?"
        recent, before = list(results[:3]), list(results[3:10])
        if len(sess) >= 2 and len(recent) == 3 and len(before) >= 3 and not sess[0].win:
            kr = sum(r.kda for r in recent) / 3.0
            kb = sum(r.kda for r in before) / len(before)
            if kb >= 1.5 and kr < 0.6 * kb:
                return "Ton KDA baisse sur 3 parties : petite pause ?"
        if len(sess) >= 6:
            return f"{len(sess)} parties d'affilée : bois de l'eau, 5 min de pause ?"
        return ""
    except Exception:
        log.debug("tilt_line failed", exc_info=True)
        return ""


__all__ = ["CompRead", "CounterTweak", "GameResult", "JungleForecast", "comp_detail", "comp_read",
           "counter_tweak", "friend_suffix", "jungle_forecast", "lane_edges", "lanes_line", "parse_history",
           "phase_scores", "play_around", "session_games", "spike_timeline", "tilt_line", "units", "win_condition"]
