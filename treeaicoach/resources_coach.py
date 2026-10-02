"""The resources a beginner forgets to use: skill points, spells, summoners, trinket, potions, gold.

Real request: "I want it to capture everything - including the spells I forget to use". Every
rule here reads ONLY the player's own state (Riot policy): the official Live Client API
(``activePlayer``: ability levels, level, gold, health; my items / summoner spells) and my own
HUD ability bar on screen (:mod:`treeaicoach.hud_abilities`: cooldown sweep, castable gold
frame, trinket charges). Never an enemy cooldown, never an input sent to the game.

THE RULES (one short verb-first card line each, through :mod:`treeaicoach.presenter`; no voice:
LESSONS 11, none of them is a danger that cannot wait)::

    rule          trigger                                                   card line                                       fight
    (the skill point and the death lessons for every level; the others for debutant / intermediaire)
    death lesson  I died and my Flash / Soin / Barrière... (HUD: no cooldown  "Utilise ton Soin avant de mourir : il était    (dead card,
                  sweep in the first dead reads) or a defensive item active / prêt"                                          after the
                  defensive ult was still up                                                                                cause line)
    skill point   level > sum of ability levels for 3 s (R first at 6/11/16) "Monte ton R : tu es niveau 6"                   yes
    potion        < 40 % HP for 3 s out of base, potion in the bag unused    "Bois ta potion : 32 % de vie"                   yes
    R in a fight  a fight starts around me and my R is ready (HUD)           "Utilise ton R dans ce combat" (or my champion's yes
                                                                             ult line, game_changers.ULT)
    teleport      Teleport ready (HUD), my team fights far from me           "Téléporte-toi en bas : 3 contre 3"              no
    gold          >= 1500 gold for 60 s out of base, no recall line said     "Rentre dépenser : 1 700 or"                     no
                  in the last 2 min by the other systems (coordinated)
    control ward  a control ward in the bag for 3 min out of base            "Pose ta balise de contrôle dans la rivière"     no
    trinket       2 trinket charges (HUD) for 45 s out of base               "Pose ta balise dans la rivière : 2 charges"     no
    red trinket   support quest finished, still the yellow trinket, in base  "Échange ta balise contre le Brouilleur..."      no

Repeats are bounded (per-rule gaps, at most a few reminders per point / per game). A rule that
needs the HUD stays silent while the bar is unreadable. Pure Python, never raises.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from treeaicoach.fmtutil import finite_loose as _f

log = logging.getLogger(__name__)

#: summoner spell id -> (death lesson line, "ready" for the teleport rule)
SPELL_LESSON: dict[str, str] = {
    "SummonerFlash": "Utilise ton Flash pour fuir : il était prêt",
    "SummonerHeal": "Utilise ton Soin avant de mourir : il était prêt",
    "SummonerBarrier": "Utilise ta Barrière avant de mourir : elle était prête",
    "SummonerHaste": "Utilise ton Fantôme pour fuir : il était prêt",
    "SummonerExhaust": "Utilise ta Fatigue sur ton tueur : elle était prête",
    "SummonerBoost": "Utilise ta Purge contre les contrôles : elle était prête",
}
SPELL_ORDER = tuple(SPELL_LESSON)          # Flash first: the beginner's escape
#: own items with a defensive / escape active -> (with article, feminine)
ITEM_ACTIVES: dict[int, tuple[str, bool]] = {
    3157: ("ton Sablier", False), 2420: ("ton Protège-bras", False), 3140: ("ta Ceinture de mercure", True),
    3139: ("ton Cimeterre", False), 6035: ("ton Aube", False), 3190: ("ton Médaillon", False),
    3152: ("ta Ceinture-roquette", True), 2065: ("ton Chant de guerre", False), 3143: ("ton Présage", False),
}
#: champions whose R saves their own life (died with it up = a lesson)
DEFENSIVE_ULT = frozenset({"Tryndamere", "Kayle", "Kindred", "Zilean", "Taric", "Lissandra"})
#: no skill points to spend (Aphelios) / R learned from level 1 (no "R at 6" rule)
NO_POINTS = frozenset({"Aphelios"})
R_AT_1 = frozenset({"Jayce", "Elise", "Nidalee", "Karma", "Udyr"})
R_LEVELS = (6, 11, 16)
POTIONS = frozenset({2003, 2031, 2033})
CONTROL_WARD = 2055
STEALTH_TRINKET = 3340
TRINKETS = frozenset({3340, 3363, 3364, 3330, 3513})
#: support quest finished (World Atlas line upgraded)
SUPPORT_DONE = frozenset({3869, 3870, 3871, 3876, 3877})
#: default key labels of the ability slots (French AZERTY game: A Z E R)
AZERTY = {"Q": "A", "W": "Z", "E": "E", "R": "R"}
QWERTY = {"Q": "Q", "W": "W", "E": "E", "R": "R"}

SKILL_GRACE_S = 3.0              # experienced players level up at once: no line before this
SKILL_REPEAT_S = 45.0
SKILL_MAX_REMINDERS = 3          # per (level, ability)
LOW_HP = 0.40
POTION_LOW_S = 3.0
POTION_REPEAT_S = 30.0
GOLD_MIN = 1500
GOLD_HELD_S = 60.0
GOLD_REPEAT_S = 120.0
GOLD_MORE = 300                  # a repeat needs this much more gold
CONTROL_HELD_S = 180.0
CONTROL_REPEAT_S = 180.0
TRINKET_FULL_S = 45.0
TRINKET_REPEAT_S = 150.0
TRINKET_MIN_GT = 150.0
FIGHT_R_GAP_S = 60.0
TP_GAP_S = 120.0
DEATH_READS_S = 4.0              # bar reads used for the death lesson: the first seconds of the death
DEATH_AFTER_CAUSE_S = 7.0        # the death-cause line (death_cause.py) keeps the card this long first
DEATH_MIN_LEFT_S = 4.0           # no lesson when the respawn is closer than this
DEATH_LESSON_MAX = 2             # the same lesson at most this many times per game
BEGINNER = frozenset({"debutant", "intermediaire"})


@dataclass(frozen=True)
class ResourceNote:
    """One card line: ``kind`` is a presenter route ("resource" / "death_cause")."""

    key: str
    text: str
    kind: str = "resource"
    urgency: int = 1              # 2: may show during a fight (short, changes the fight)
    ttl: float = 10.0
    topic: str = ""


@dataclass
class ResourceInputs:
    """One coaching tick (all optional: a missing source switches its rules off)."""

    t: float                      # monotonic seconds
    gt: float                     # game time
    game: Any = None              # live_client.GameInfo
    bar: Any = None               # hud_abilities.BarRead (valid or not) / None
    in_base: bool = False
    fight: bool = False           # a fight around me (tactics)
    alarm: bool = False           # gank / personal danger now
    skill: str = "intermediaire"
    recall_recent: bool = False   # a recall line was shown by another system lately
    ward_recent: bool = False     # a ward line was shown lately
    macro_busy: bool = False      # a macro call holds the card
    objective_soon: bool = False  # my objective soon: never "rentre"
    cause_shown: bool = False     # the death-cause line is on the dead card
    remote_fight: tuple[str, int, int] | None = None   # (where, allies, enemies) far from me
    my_lane: str | None = None    # "top" / "mid" / "bot" / "jungle"


@dataclass
class _Death:
    t: float
    gt: float
    reads: list = field(default_factory=list)          # BarRead during the first seconds
    before: Any = None                                 # last alive read (<= 3 s before)
    note: ResourceNote | None = None
    decided: bool = False
    done: bool = False


def _abilities(game: Any) -> dict[str, int]:
    info = getattr(game, "active_info", None) or {}
    ab = info.get("abilities") if isinstance(info, dict) else None
    if not isinstance(ab, dict):
        return {}
    out = {}
    for k in ("Q", "W", "E", "R"):
        try:
            out[k] = max(0, int(ab.get(k, 0) or 0))
        except (TypeError, ValueError):
            out[k] = 0
    return out


def unspent_points(level: int, abilities: dict[str, int], alias: str = "") -> int:
    """Skill points not spent yet (0 when unknown / not applicable)."""
    if not abilities or alias in NO_POINTS:
        return 0
    return max(0, int(level) - sum(abilities.values()))


def next_ability(level: int, abilities: dict[str, int], alias: str = "") -> str | None:
    """The ability to put the point in: R when it can rise (6 / 11 / 16), else a basic ability
    not learned yet (levels 1-3), else the basic ability already the highest (the one the
    player maxes). None when nothing can rise."""
    lvl = int(level)
    if alias not in R_AT_1:
        allowed_r = sum(1 for x in R_LEVELS if lvl >= x)
        if abilities.get("R", 0) < allowed_r:
            return "R"
    cap = min(5, (lvl + 1) // 2)
    basics = [k for k in ("Q", "W", "E") if abilities.get(k, 0) < cap]
    if not basics:
        return None
    zero = [k for k in basics if abilities.get(k, 0) == 0]
    if zero and lvl <= 3:
        return zero[0]
    order = {"Q": 0, "E": 1, "W": 2}
    return sorted(basics, key=lambda k: (-abilities.get(k, 0), order[k]))[0]


def _hp(game: Any) -> float | None:
    st = getattr(game, "champion_stats", None) or {}
    cur, mx = _f(st.get("currentHealth")), _f(st.get("maxHealth"))
    if cur is None or not mx:
        return None
    return max(0.0, min(1.0, cur / mx))


def _counts(me: Any) -> dict[int, int]:
    c = getattr(me, "item_counts", None)
    if isinstance(c, dict) and c:
        return {int(k): int(v) for k, v in c.items()}
    out: dict[int, int] = {}
    for i in getattr(me, "items", None) or []:
        try:
            out[int(i)] = out.get(int(i), 0) + 1
        except (TypeError, ValueError):
            pass
    return out


def _slot_of(me: Any, item_id: int) -> int | None:
    slots = getattr(me, "item_slots", None)
    if isinstance(slots, dict):
        for s, i in slots.items():
            if int(i) == int(item_id):
                return int(s)
    return None


def _fmt_gold(g: float) -> str:
    """``1720`` -> ``"1 720"`` (same separator as the other gold lines, reminders.format_gold)."""
    try:
        from treeaicoach.reminders import format_gold

        return format_gold(g)
    except Exception:
        return str(int(g))


class ResourcesCoach:
    """Stateful (one per game). :meth:`update` returns at most one note per tick; the engine
    calls :meth:`shown` when the note reached the card (a dropped note is offered again)."""

    def __init__(self, labels: dict[str, str] | None = None) -> None:
        self.labels = dict(labels or AZERTY)
        self.reset()

    def reset(self) -> None:
        self._last_gt: float | None = None
        self._was_dead = False
        self._death: _Death | None = None
        self._alive_reads: deque = deque(maxlen=8)        # (t, BarRead) while alive
        self._lessons: dict[str, int] = {}
        self._shown: dict[str, float] = {}                # rule topic -> last shown (t)
        self._skill_seen: dict[tuple[int, str], tuple[float, int]] = {}   # (level, ab) -> (first t, reminders)
        self._low_since: float | None = None
        self._low_potions: int | None = None
        self._hp_hist: deque = deque(maxlen=16)
        self._gold_since: float | None = None
        self._gold_said: float = 0.0
        self._cw_held_s = 0.0
        self._cw_count = 0
        self._cw_last_t: float | None = None
        self._trinket_full_since: float | None = None
        self._fight_prev = False
        self._fight_start: float | None = None
        self._red_shown = 0
        self._red_visit: bool = False
        self._gold_now = 0.0
        self.history: list[tuple[float, str]] = []

    # ------------------------------------------------------------------ public
    def shown(self, note: ResourceNote, t: float) -> None:
        """The note reached the card (repeat bookkeeping)."""
        try:
            self._shown[note.topic or note.key] = float(t)
            self.history.append((float(t), note.text))
            del self.history[:-100]
            if note.topic == "skill":
                for k, (t0, n) in list(self._skill_seen.items()):
                    if note.key.endswith(f":{k[0]}:{k[1]}"):
                        self._skill_seen[k] = (t0, n + 1)
            if note.topic.startswith("death:"):
                self._lessons[note.topic] = self._lessons.get(note.topic, 0) + 1
                if self._death is not None:
                    self._death.done = True
            if note.topic == "gold":
                self._gold_said = self._gold_now
            if note.topic == "red_trinket":
                self._red_shown += 1
                self._red_visit = True                      # once per base visit
        except Exception:
            log.debug("resources shown failed", exc_info=True)

    def update(self, inp: ResourceInputs) -> ResourceNote | None:
        """The best note for this tick, or None. Never raises."""
        try:
            return self._update(inp)
        except Exception:
            log.debug("resources coach failed", exc_info=True)
            return None

    # ------------------------------------------------------------------ internals
    def _gap_ok(self, topic: str, t: float, gap: float) -> bool:
        last = self._shown.get(topic)
        return last is None or not 0.0 <= t - last < gap

    def _update(self, inp: ResourceInputs) -> ResourceNote | None:
        game = inp.game
        me = getattr(game, "me", None)
        if me is None:
            return None
        gt, t = float(inp.gt), float(inp.t)
        if self._last_gt is not None and gt < self._last_gt - 5.0:
            self.reset()                                   # new game / replay rewound
        self._last_gt = gt
        self._gold_now = _f(getattr(game, "current_gold", None)) or 0.0
        dead = bool(getattr(me, "is_dead", False))
        bar = inp.bar if inp.bar is not None and getattr(inp.bar, "valid", False) else None
        cands: list[tuple[int, ResourceNote]] = []

        # ---- death lesson (dead card)
        if dead and not self._was_dead:
            before = None
            for rt, rd in reversed(self._alive_reads):
                if 0.0 <= t - rt <= 3.0:
                    before = rd
                    break
            self._death = _Death(t=t, gt=gt, before=before)
        if not dead:
            if self._was_dead:
                self._death = None
            if bar is not None:
                self._alive_reads.append((t, bar))
        self._was_dead = dead
        if dead and self._death is not None and not self._death.done:
            note = self._death_lesson(inp, me, bar)
            if note is not None:
                cands.append((100, note))
        if dead:
            self._fight_prev = False
            self._low_since = self._gold_since = self._trinket_full_since = None
            return self._pick(cands)

        hp = _hp(game)
        counts = _counts(me)
        ab = _abilities(game)
        alias = str(getattr(me, "champion_alias", "") or "")
        level = int(getattr(me, "level", 1) or 1)
        calm = not (inp.fight or inp.alarm)

        # ---- skill point (Live Client)
        n = self._skill_note(inp, ab, level, alias)
        if n is not None:
            cands.append((90 if n.key.startswith("res:skill:R") else 70, n))

        # ---- potion
        n = self._potion_note(inp, hp, self._usable_potions(me, counts, bar))
        if n is not None and inp.skill in BEGINNER:
            cands.append((85, n))

        # ---- R at the start of a fight (HUD)
        n = self._fight_r_note(inp, bar, ab, alias)
        if n is not None:
            cands.append((80, n))

        if calm and inp.skill not in BEGINNER:
            calm = False                    # advanced players: only the skill point / death lessons (LESSONS 5)
        if calm:
            n = self._tp_note(inp, bar, me)
            if n is not None:
                cands.append((60, n))
            n = self._gold_note(inp)
            if n is not None:
                cands.append((50, n))
            n = self._control_ward_note(inp, counts)
            if n is not None:
                cands.append((40, n))
            n = self._trinket_note(inp, bar, me, counts)
            if n is not None:
                cands.append((30, n))
            n = self._red_trinket_note(inp, me, counts)
            if n is not None:
                cands.append((20, n))
        else:
            self._gold_since = None
            self._trinket_full_since = None
        return self._pick(cands)

    @staticmethod
    def _pick(cands: list[tuple[int, ResourceNote]]) -> ResourceNote | None:
        if not cands:
            return None
        return max(cands, key=lambda c: c[0])[1]

    # ---- rules
    def _death_lesson(self, inp: ResourceInputs, me: Any, bar: Any) -> ResourceNote | None:
        d = self._death
        if d is None:
            return None
        t = float(inp.t)
        if bar is not None and t - d.t <= DEATH_READS_S:
            d.reads.append(bar)
        if not d.decided:
            if t - d.t < DEATH_READS_S and len(d.reads) < 3:
                return None
            d.decided = True
            d.note = self._decide_lesson(d, me, inp.game)
        if d.note is None:
            return None
        left = _f(getattr(me, "respawn_timer", None))
        if left is not None and 0.0 < left < DEATH_MIN_LEFT_S:
            return None
        if inp.cause_shown and t - d.t < DEATH_AFTER_CAUSE_S:
            return None                                     # the death cause first, then this
        if self._lessons.get(d.note.topic, 0) >= DEATH_LESSON_MAX:
            return None
        return d.note

    def _decide_lesson(self, d: _Death, me: Any, game: Any) -> ResourceNote | None:
        if len(d.reads) < 2:
            return None

        def up(slot: str) -> bool:
            """Not on cooldown in every dead read (and not in the last alive read)."""
            states = [r.get(slot) for r in d.reads]
            if any(s is None for s in states) or any(s.cooldown for s in states):
                return False
            if d.before is not None:
                b = d.before.get(slot)
                if b is not None and b.cooldown:
                    return False
            return True

        ids = list(getattr(me, "spell_ids", None) or ())
        for sid in SPELL_ORDER:
            if sid in ids[:2]:
                slot = "D" if ids.index(sid) == 0 else "F"
                if up(slot):
                    return ResourceNote(f"death:res:{sid}:{int(d.gt)}", SPELL_LESSON[sid], "death_cause", 2, 30.0,
                                        topic=f"death:{sid}")
        from treeaicoach.hud_abilities import ITEM_CELL

        for iid, (name, fem) in ITEM_ACTIVES.items():
            slot = _slot_of(me, iid)
            if slot is None:
                continue
            cell = ITEM_CELL.get(slot)
            if cell is None or not up(cell):
                continue
            # an item active ready shows a gold frame even while dead: require it in the last alive read
            b = d.before.get(cell) if d.before is not None else None
            if b is None or not b.castable:
                continue
            text = f"Utilise {name} avant de mourir : {'elle était prête' if fem else 'il était prêt'}"
            if len(text) <= 60:
                return ResourceNote(f"death:res:item{iid}:{int(d.gt)}", text, "death_cause", 2, 30.0,
                                    topic=f"death:item{iid}")
        alias = str(getattr(me, "champion_alias", "") or "")
        if alias in DEFENSIVE_ULT and _abilities(game).get("R", 0) >= 1 and up("R"):
            b = d.before.get("R") if d.before is not None else None
            if b is not None and b.castable:
                return ResourceNote(f"death:res:R:{int(d.gt)}", "Utilise ton R avant de mourir : il était prêt",
                                    "death_cause", 2, 30.0, topic="death:R")
        return None

    def _skill_note(self, inp: ResourceInputs, ab: dict[str, int], level: int, alias: str) -> ResourceNote | None:
        if unspent_points(level, ab, alias) <= 0:
            return None
        which = next_ability(level, ab, alias)
        if which is None:
            return None
        if inp.skill not in BEGINNER and which != "R":
            return None                                      # advanced players do not forget basics
        t = float(inp.t)
        key = (level, which)
        t0, n = self._skill_seen.get(key, (t, 0))
        self._skill_seen.setdefault(key, (t0, n))
        if len(self._skill_seen) > 60:
            for k in list(self._skill_seen)[:30]:
                del self._skill_seen[k]
        if t - t0 < SKILL_GRACE_S or n >= SKILL_MAX_REMINDERS:
            return None
        if not self._gap_ok("skill", t, SKILL_REPEAT_S):
            return None
        if which == "R":
            text = f"Monte ton R : tu es niveau {level}"
        else:
            text = f"Monte ton {self.labels.get(which, which)} : tu as un point libre"
        return ResourceNote(f"res:skill:{level}:{which}", text, "resource", 2, 10.0, topic="skill")

    @staticmethod
    def _usable_potions(me: Any, counts: dict[int, int], bar: Any) -> int:
        """Health potions (their stack count) + refillable / corrupting potions with charges left:
        the Live Client does not give their charges, the HUD does (an empty one has no gold frame)."""
        n = counts.get(2003, 0)
        for iid in (2031, 2033):
            if counts.get(iid, 0) <= 0 or bar is None:
                continue
            from treeaicoach.hud_abilities import ITEM_CELL

            slot = _slot_of(me, iid)
            st = bar.get(ITEM_CELL.get(slot, "")) if slot is not None else None
            if st is not None and st.castable:
                n += 1
        return n

    def _potion_note(self, inp: ResourceInputs, hp: float | None, pots: int) -> ResourceNote | None:
        t = float(inp.t)
        if hp is not None:
            self._hp_hist.append((t, hp))
        if hp is None or hp >= LOW_HP or pots <= 0 or inp.in_base or inp.alarm:
            self._low_since = None
            self._low_potions = None
            return None
        if self._low_since is None:
            self._low_since, self._low_potions = t, pots
            return None
        if self._low_potions is not None and pots < self._low_potions:
            self._low_since, self._low_potions = t, pots    # just drank one
            return None
        # regenerating (a potion / a heal running): health rose >= 3 % over the last ~4 s
        old = [h for (tt, h) in self._hp_hist if 3.0 <= t - tt <= 6.0]
        if old and hp - old[-1] >= 0.03:
            return None
        if t - self._low_since < POTION_LOW_S or not self._gap_ok("potion", t, POTION_REPEAT_S):
            return None
        return ResourceNote(f"res:potion:{int(inp.gt)}", f"Bois ta potion : {int(round(hp * 100))} % de vie",
                            "resource", 2, 8.0, topic="potion")

    def _fight_r_note(self, inp: ResourceInputs, bar: Any, ab: dict[str, int], alias: str) -> ResourceNote | None:
        t = float(inp.t)
        started = inp.fight and not self._fight_prev
        self._fight_prev = bool(inp.fight)
        if started:
            self._fight_start = t
        if not inp.fight or self._fight_start is None or t - self._fight_start > 8.0:
            return None
        if inp.skill not in BEGINNER or bar is None or inp.alarm:
            return None
        if ab and ab.get("R", 0) < 1:
            return None
        if bar.ready("R") is not True or not self._gap_ok("fight_r", t, FIGHT_R_GAP_S):
            return None
        text = "Utilise ton R dans ce combat"
        try:
            from treeaicoach.game_changers import ult_line

            u = ult_line(alias, None)
            if u is not None and len(u[0]) <= 60 and u[0].split(" ", 1)[0] in ("Lance", "Utilise", "Garde", "Engage",
                                                                                  "Mets"):
                text = u[0]
        except Exception:
            pass
        return ResourceNote(f"res:fight_r:{int(inp.gt)}", text, "resource", 2, 8.0, topic="fight_r")

    def _tp_note(self, inp: ResourceInputs, bar: Any, me: Any) -> ResourceNote | None:
        rf = inp.remote_fight
        if rf is None or bar is None or inp.skill not in BEGINNER or inp.in_base:
            return None
        ids = list(getattr(me, "spell_ids", None) or ())
        if "SummonerTeleport" not in ids[:2]:
            return None
        slot = "D" if ids.index("SummonerTeleport") == 0 else "F"
        if bar.ready(slot) is not True or not self._gap_ok("tp", float(inp.t), TP_GAP_S):
            return None
        where, allies, enemies = rf
        text = f"Téléporte-toi {where} : {allies} contre {enemies}"
        return ResourceNote(f"res:tp:{int(inp.gt)}", text, "resource", 1, 10.0, topic="tp")

    def _gold_note(self, inp: ResourceInputs) -> ResourceNote | None:
        t, gold = float(inp.t), self._gold_now
        if gold < GOLD_MIN or inp.in_base:
            self._gold_since = None
            return None
        if self._gold_since is None:
            self._gold_since = t
        if t - self._gold_since < GOLD_HELD_S:
            return None
        if inp.recall_recent or inp.macro_busy or inp.objective_soon:
            return None
        if not self._gap_ok("gold", t, GOLD_REPEAT_S) or (self._gold_said and gold < self._gold_said + GOLD_MORE):
            return None
        return ResourceNote(f"res:gold:{int(inp.gt)}", f"Rentre dépenser : {_fmt_gold(gold)} or", "resource", 1,
                            10.0, topic="gold")

    def _control_ward_note(self, inp: ResourceInputs, counts: dict[int, int]) -> ResourceNote | None:
        t = float(inp.t)
        n = counts.get(CONTROL_WARD, 0)
        if n < self._cw_count or n == 0:
            self._cw_held_s = 0.0                            # placed one (or none in the bag)
        self._cw_count = n
        dt = 0.0 if self._cw_last_t is None else max(0.0, min(2.0, t - self._cw_last_t))
        self._cw_last_t = t
        if n == 0 or inp.in_base:
            return None
        self._cw_held_s += dt
        if self._cw_held_s < CONTROL_HELD_S or inp.ward_recent or not self._gap_ok("control_ward", t, CONTROL_REPEAT_S):
            return None
        return ResourceNote(f"res:control:{int(inp.gt)}", "Pose ta balise de contrôle dans la rivière", "resource", 1,
                            10.0, topic="control_ward")

    def _trinket_note(self, inp: ResourceInputs, bar: Any, me: Any, counts: dict[int, int]) -> ResourceNote | None:
        t = float(inp.t)
        if bar is None or counts.get(STEALTH_TRINKET, 0) <= 0 or inp.in_base or inp.gt < TRINKET_MIN_GT:
            self._trinket_full_since = None
            return None
        if bar.trinket_charges != 2:
            if bar.trinket_charges is not None:
                self._trinket_full_since = None
            return None
        if self._trinket_full_since is None:
            self._trinket_full_since = t
        if t - self._trinket_full_since < TRINKET_FULL_S or inp.ward_recent:
            return None
        if not self._gap_ok("trinket", t, TRINKET_REPEAT_S):
            return None
        where = "dans la rivière" if inp.my_lane in (None, "top", "mid", "bot", "jungle") else "devant toi"
        return ResourceNote(f"res:trinket:{int(inp.gt)}", f"Pose ta balise {where} : tu as 2 charges", "resource", 1,
                            10.0, topic="trinket")

    def _red_trinket_note(self, inp: ResourceInputs, me: Any, counts: dict[int, int]) -> ResourceNote | None:
        if not inp.in_base:
            self._red_visit = False
            return None
        if self._red_visit or self._red_shown >= 2:
            return None
        if counts.get(STEALTH_TRINKET, 0) <= 0 or not any(counts.get(i, 0) for i in SUPPORT_DONE):
            return None
        return ResourceNote(f"res:red_trinket:{int(inp.gt)}", "Échange ta balise contre le Brouilleur oraculaire",
                            "resource", 1, 12.0, topic="red_trinket")


# =============================================================================== engine glue
BAR_PERIOD_S = 0.5               # HUD bar reads: <= 2 Hz
CAL_RETRY_S = 30.0               # a failed calibration is retried this late
REMOTE_FIGHT_UV = 0.08           # champions this close to a fight centre fight there
REMOTE_MIN_DIST = 0.35           # a fight this far from me is "elsewhere"
CARD_READ_S = 5.5                # a new line waits until the card's current line was shown this long
PENDING_S = 9.0                  # an offered line must reach the card within this (card gap / dwell)
RETRY_S = 12.0                   # a line that did not reach the card is offered again this late
_LANE_WORD = {"top": "en haut", "mid": "au milieu", "bot": "en bas"}


class _EngineGlue:
    """Per-engine state: the coach, the HUD bar reader (calibrated on a background thread)."""

    def __init__(self, labels: dict[str, str]) -> None:
        self.coach = ResourcesCoach(labels)
        self.reader: Any = None
        self.cal_t = -1e9
        self.cal_size: tuple[int, int] | None = None
        self.cal_thread: Any = None
        self.read_t = -1e9
        self.bar: Any = None
        self.gt = 0.0
        self.pending: tuple[ResourceNote, float] | None = None   # offered, waiting for the card
        self.backoff: dict[str, float] = {}


def _labels(cfg: Any) -> dict[str, str]:
    lay = str(getattr(cfg, "ability_keys", "azerty") or "azerty").lower()
    return dict(QWERTY if lay == "qwerty" else AZERTY)


def _engine_bar(eng: Any, glue: _EngineGlue, t: float) -> Any:
    """My HUD ability bar (hud_abilities.BarRead) or None: ``eng.resources_bar_source(t)`` when
    set (simulations / tests), else a small screen grab at <= 2 Hz of the calibrated bar, only
    while the game window is visible and not covered (LESSONS 9). Never raises."""
    src = getattr(eng, "resources_bar_source", None)
    if callable(src):
        try:
            return src(t)
        except Exception:
            return None
    if t - glue.read_t < BAR_PERIOD_S and t >= glue.read_t:
        return glue.bar
    glue.read_t = t
    glue.bar = None
    try:
        win = getattr(eng, "_window", None)
        if win is None or getattr(eng, "_paused", None) or getattr(eng, "_occluded", False) \
                or getattr(eng, "_frame_source", None) is not None:
            return None
        from treeaicoach.capture import Rect
        from treeaicoach.hud_abilities import AbilityBarReader

        if glue.reader is None:
            glue.reader = AbilityBarReader()
        rd = glue.reader
        size = (int(win.w), int(win.h))
        if glue.cal_size != size:
            th = glue.cal_thread
            if th is not None and th.is_alive():
                return None
            if glue.cal_thread is not None and rd.layout is not None and rd._size == (size[1], size[0]):
                glue.cal_size, glue.cal_thread = size, None          # the background fit just finished
            elif t - glue.cal_t >= CAL_RETRY_S or t < glue.cal_t:
                glue.cal_t = t
                screen = eng._grabber().grab(win)
                if screen is None:
                    return None
                from treeaicoach.capture import is_black_frame

                if is_black_frame(screen):
                    return None
                hr = getattr(eng, "_hud_reader", None)
                portrait = hr.location if hr is not None and getattr(hr, "_size", None) == screen.shape[:2] else None
                import threading

                glue.cal_thread = threading.Thread(target=rd.calibrate, args=(screen, portrait),
                                                   name="treeai-hud-bar", daemon=True)
                glue.cal_thread.start()
                return None
            else:
                return None
        roi = rd.roi()
        if roi is None:
            return None
        x, y, w, h = roi
        rect = Rect(win.x + x, win.y + y, w, h)
        occl = getattr(eng, "_occlusion", None)
        if callable(occl) and occl(rect) is True:
            return None
        patch = eng._grabber().grab(rect)
        glue.bar = rd.read_patch(patch)
        return glue.bar
    except Exception:
        log.debug("HUD bar grab failed", exc_info=True)
        return None


def _remote_fight(eng: Any, me_uv: Any) -> tuple[str, int, int] | None:
    """``(where, allies, enemies)`` of a fight of my team far from me (visible minimap icons)."""
    tr = getattr(eng, "_tracker", None)
    if tr is None or me_uv is None:
        return None
    try:
        from treeaicoach import geometry

        al = [a.position() for a in tr.allies(visible_only=True) if getattr(a, "relation", "") != "self"]
        en = [e.position() for e in tr.enemies(visible_only=True)]
        al = [p for p in al if p is not None]
        en = [p for p in en if p is not None]
        best = None
        for c in en:
            ne = sum(1 for p in en if geometry.dist(p, c) <= REMOTE_FIGHT_UV)
            na = sum(1 for p in al if geometry.dist(p, c) <= REMOTE_FIGHT_UV)
            if ne >= 2 and na >= 2 and geometry.dist(c, me_uv) >= REMOTE_MIN_DIST:
                if best is None or ne + na > best[1] + best[2]:
                    best = (c, na, ne)
        if best is None:
            return None
        lane = geometry.lane_of(geometry.classify_zone(*best[0]))
        where = _LANE_WORD.get(str(lane or ""))
        if where is None:
            return None
        return where, int(best[1]) + 1, int(best[2])          # + me after the teleport
    except Exception:
        return None


def engine_tick(eng: Any, t: float, game: Any, threat: int = 0) -> ResourceNote | None:
    """One coaching tick of the engine (``CoachingMixin``): inputs from the engine's systems,
    the coach's best note offered to the presenter; the shown one goes to the HUD card.
    Never raises."""
    try:
        if game is None or getattr(game, "me", None) is None or not getattr(game, "is_summoners_rift", True):
            return None
        cfg = getattr(eng, "_cfg", None)
        if not getattr(cfg, "resource_coach", True):
            return None
        glue = getattr(eng, "_res_glue", None)
        gt = _f(getattr(game, "game_time", None)) or 0.0
        if glue is None or gt + 5.0 < glue.gt:
            glue = eng._res_glue = _EngineGlue(_labels(cfg))
        glue.gt = gt
        bar = _engine_bar(eng, glue, t)
        tac = getattr(eng, "_tactics", None)
        fight = bool(tac is not None and tac.in_fight())
        try:
            alarm = bool(eng._alarm_now(t)) or int(threat) >= 2
        except Exception:
            alarm = int(threat) >= 2
        me_tr = eng._tracker.me() if getattr(eng, "_tracker", None) is not None else None
        me_uv = me_tr.position() if me_tr is not None else None
        in_base = False
        lane = None
        if me_uv is not None:
            from treeaicoach import geometry

            z = geometry.classify_zone(*me_uv)
            in_base = geometry.is_base(z) and geometry.zone_owner(z) == game.my_team
            lane = geometry.lane_of(z)
        mc = tac.macro_active() if tac is not None else None
        rot = getattr(eng, "_tip_rotator", None)
        tip_id = str(rot.current_id() or "") if rot is not None else ""
        last_recall = getattr(eng, "_recall_topic_t", None)
        recall_recent = (last_recall is not None and 0.0 <= t - last_recall < getattr(eng, "RECALL_TOPIC_S", 120.0)) \
            or tip_id in getattr(eng, "RECALL_TIP_IDS", ()) or (mc is not None and getattr(mc, "kind", "") == "wave_recall")
        try:
            recall_recent = recall_recent or bool(eng._base_recent(t))
            ward_recent = bool(eng._ward_recent(t))
        except Exception:
            ward_recent = False
        try:
            obj_soon = tip_id == "obj_stay" or eng._objective_line(t) is not None
        except Exception:
            obj_soon = tip_id == "obj_stay"
        msg = getattr(eng, "_text_msg", None)
        cause = getattr(eng, "_text_kind", None) == "death_cause" and msg is not None \
            and msg[1] not in {h[1] for h in glue.coach.history[-5:]}
        inp = ResourceInputs(t=t, gt=gt, game=game, bar=bar, in_base=in_base, fight=fight, alarm=alarm,
                             skill=str(getattr(cfg, "skill_level", "intermediaire") or "intermediaire"),
                             recall_recent=recall_recent, ward_recent=ward_recent, macro_busy=mc is not None,
                             objective_soon=obj_soon, cause_shown=cause,
                             remote_fight=_remote_fight(eng, me_uv) if bar is not None else None, my_lane=lane)
        note = glue.coach.update(inp)                          # every tick (timers, death reads)
        done = _confirm(eng, glue, t)
        if done is not None or glue.pending is not None or note is None:
            return done
        if t < glue.backoff.get(note.topic or note.key, -1e9):
            return None
        if _offer(eng, note, t):
            glue.pending = (note, t)
            return _confirm(eng, glue, t)
        return None
    except Exception:
        log.debug("resources engine tick failed", exc_info=True)
        return None


def _confirm(eng: Any, glue: _EngineGlue, t: float) -> ResourceNote | None:
    """The offered note counts as shown only once the HUD card really shows it (another system may
    write the card line in the same tick); not shown within :data:`PENDING_S`: offered again later."""
    if glue.pending is None:
        return None
    note, t_off = glue.pending
    shown = getattr(eng, "_hud_shown", None)
    if shown is not None and shown[0] == note.text:
        glue.pending = None
        glue.coach.shown(note, t)
        if note.topic == "gold":
            eng._recall_topic_t = t                           # one recall message per trip, all systems
        if note.topic in ("trinket", "control_ward"):
            eng._ward_topic_t = t
        return note
    if not 0.0 <= t - t_off < PENDING_S:
        glue.pending = None
        glue.backoff[note.topic or note.key] = t + RETRY_S
    return None


def _offer(eng: Any, note: ResourceNote, t: float) -> bool:
    """Route the note through the presenter; a PANEL decision writes the HUD card line."""
    try:
        from treeaicoach import presenter as prs

        shown = getattr(eng, "_hud_shown", None)
        age = eng._card_age(t) if callable(getattr(eng, "_card_age", None)) else None
        if shown is not None and shown[0] and shown[0] != note.text and age is not None and age < CARD_READ_S:
            return False                                      # the card's line is read first (no flicker)
        pr = getattr(eng, "_presenter", None)
        if pr is not None:
            d = pr.offer(prs.Message(note.kind, note.text, "", urgency=note.urgency, ttl=note.ttl,
                                     topic=note.key), eng._presenter_ctx(t))
            if d.channel != prs.PANEL:
                return False
        eng._text_msg, eng._text_kind = (t, note.text), note.kind
        msgs = getattr(eng, "text_messages", None)
        if isinstance(msgs, list):
            msgs.append((t, note.kind, note.text))
            del msgs[:-100]
        return True
    except Exception:
        log.debug("resource offer failed", exc_info=True)
        return False


__all__ = ["engine_tick", "ResourcesCoach", "ResourceInputs", "ResourceNote", "unspent_points", "next_ability", "SPELL_LESSON",
           "ITEM_ACTIVES", "AZERTY", "QWERTY"]
