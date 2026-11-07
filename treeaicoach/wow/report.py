"""Post-fight and session report from combat log events (DPS, deaths with cause, mistakes, pet uptime).

Feed :class:`CombatEvent` lists (from :mod:`treeaicoach.wow.combatlog`) to :class:`SessionReport`.
A fight starts at the first damage exchanged between our side (me, my pet, my group) and a hostile
unit, and ends after ``gap`` seconds without such damage. Pure logic, French texts, never raises.
"""
from __future__ import annotations

import logging
from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from treeaicoach.wow.combatlog import (ENV_FR, CombatEvent, is_friendly_side, is_hostile, is_me, is_my_pet,
                                       is_party, short_name)

log = logging.getLogger(__name__)

FIGHT_GAP_S = 5.0
LOW_HP_PCT = 35.0
DEATH_RECAP_S = 6.0


def _mmss(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f"{sec // 60}:{sec % 60:02d}"


def _num(n: float) -> str:
    """French thousands separator: 12345 -> '12 345'."""
    return f"{int(round(n)):,}".replace(",", " ")


@dataclass
class Hit:
    t: float
    source: str
    spell: str
    amount: int


@dataclass
class Death:
    t: float
    name: str
    who: str                     # "me" | "pet" | "party"
    killer: str = ""
    spell: str = ""
    recent: list[Hit] = field(default_factory=list)

    @property
    def damage_recent(self) -> int:
        return sum(h.amount for h in self.recent)

    def cause_fr(self) -> str:
        if not self.killer and not self.spell:
            return f"{self.name} est mort"
        how = f" ({self.spell})" if self.spell else ""
        txt = f"{self.name} tué par {self.killer or '?'}{how}"
        if self.recent:
            span = max(0.1, self.t - self.recent[0].t)
            txt += f", {_num(self.damage_recent)} dégâts en {span:.0f} s"
        return txt


@dataclass
class Fight:
    start: float
    end: float
    dmg_me: int = 0
    dmg_pet: int = 0
    dmg_party: Counter = field(default_factory=Counter)
    taken_me: int = 0
    taken_pet: int = 0
    heal_me: int = 0
    deaths: list[Death] = field(default_factory=list)
    kills: int = 0
    mobs: set = field(default_factory=set)
    max_adds: int = 0
    min_hp_me: float | None = None
    min_hp_pet: float | None = None
    cc_broken: list[str] = field(default_factory=list)
    aggro_friends: Counter = field(default_factory=Counter)
    biggest_hit: Hit | None = None
    pet_seen: bool = False
    pet_alive_s: float = 0.0
    pet_active_bins: set = field(default_factory=set)
    spells: Counter = field(default_factory=Counter)  # my damage per ability

    @property
    def duration(self) -> float:
        return max(1.0, self.end - self.start)

    @property
    def dps(self) -> float:
        return (self.dmg_me + self.dmg_pet) / self.duration

    @property
    def pet_uptime(self) -> float | None:
        """Fraction (0..1) of the fight with the pet alive; None if no pet."""
        if not self.pet_seen:
            return None
        return max(0.0, min(1.0, self.pet_alive_s / self.duration))

    @property
    def pet_active(self) -> float | None:
        """Fraction of 2-second slices of the fight in which the pet dealt damage."""
        if not self.pet_seen:
            return None
        bins = max(1, int(self.duration // 2) + 1)
        return min(1.0, len(self.pet_active_bins) / bins)

    def mistakes(self) -> list[str]:
        out: list[str] = []
        for d in self.deaths:
            if d.who == "me":
                out.append(f"Tu es mort : {d.cause_fr()}.")
            elif d.who == "pet":
                out.append(f"Familier mort à {_mmss(d.t - self.start)} : {d.cause_fr()}.")
        if self.min_hp_me is not None and self.min_hp_me < LOW_HP_PCT and not any(d.who == "me" for d in self.deaths):
            out.append(f"Ta vie est descendue à {self.min_hp_me:.0f} %.")
        if self.max_adds >= 3:
            out.append(f"{self.max_adds} monstres engagés en même temps : tire-les un par un.")
        out.extend(self.cc_broken)
        for name, n in self.aggro_friends.most_common(2):
            out.append(f"{name} a pris l'aggro ({n} coups).")
        act = self.pet_active
        if act is not None and self.duration >= 8 and (self.pet_uptime or 0) > 0.8 and act < 0.4:
            out.append(f"Familier peu actif ({act * 100:.0f} % du combat) : /petattack.")
        return out

    def summary_fr(self) -> str:
        parts = [f"Combat {_mmss(self.duration)}", f"{self.dps:.0f} DPS"]
        if self.dmg_pet:
            share = 100 * self.dmg_pet / max(1, self.dmg_me + self.dmg_pet)
            parts.append(f"familier {share:.0f} %")
        if self.kills:
            parts.append(f"{self.kills} tué{'s' if self.kills > 1 else ''}")
        up = self.pet_uptime
        if up is not None and up < 0.999:
            parts.append(f"familier en vie {up * 100:.0f} %")
        return ", ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start, "duration": round(self.duration, 1), "dps": round(self.dps, 1),
            "damage_me": self.dmg_me, "damage_pet": self.dmg_pet, "damage_party": dict(self.dmg_party),
            "taken_me": self.taken_me, "kills": self.kills, "mobs": len(self.mobs), "max_adds": self.max_adds,
            "min_hp_me": self.min_hp_me, "pet_uptime": self.pet_uptime, "pet_active": self.pet_active,
            "deaths": [{"name": d.name, "who": d.who, "cause": d.cause_fr()} for d in self.deaths],
            "mistakes": self.mistakes(), "top_spells": self.spells.most_common(5),
        }


class SessionReport:
    """Accumulates events into fights and a session summary. ``feed`` may be called with any batch."""

    def __init__(self, gap: float = FIGHT_GAP_S, keep_fights: int = 200) -> None:
        self.gap = float(gap)
        self.fights: deque[Fight] = deque(maxlen=max(1, keep_fights))
        self.current: Fight | None = None
        self.env_damage: Counter = Counter()        # "noyade" -> damage
        self.deaths_out_of_fight: list[Death] = []
        self.me_name = ""
        self.pet_name = ""
        self._pet_guid = ""
        self._pet_alive: bool | None = None
        self._last_t: float | None = None
        self._recent: dict[str, deque[Hit]] = {}
        self._engaged: dict[str, float] = {}         # hostile guid -> last exchange time

    # --- feeding --------------------------------------------------------------------------------
    def feed(self, events: Iterable[CombatEvent]) -> list[Fight]:
        """Process events; returns the fights that ENDED during this batch."""
        ended: list[Fight] = []
        for ev in events:
            try:
                f = self._one(ev)
                if f is not None:
                    ended.append(f)
            except Exception as e:
                log.debug("report: event skipped (%s)", e)
        return ended

    def flush(self, now: float | None = None) -> Fight | None:
        """Close the current fight if ``now`` is past its gap (or unconditionally if ``now`` is None)."""
        f = self.current
        if f is None:
            return None
        if now is not None and now - f.end < self.gap:
            return None
        return self._close()

    def _close(self) -> Fight | None:
        f, self.current = self.current, None
        if f is not None:
            self.fights.append(f)
            self._engaged.clear()
        return f

    def _advance_pet(self, f: Fight | None, t: float) -> None:
        if f is not None and self._last_t is not None and self._pet_alive:
            lo = max(self._last_t, f.start)
            if t > lo:
                f.pet_alive_s += t - lo
        self._last_t = t

    def _one(self, ev: CombatEvent) -> Fight | None:
        ended = None
        if self.current is not None and ev.t - self.current.end > self.gap:
            self._advance_pet(self.current, self.current.end)
            ended = self._close()
        f = self.current
        self._advance_pet(f, ev.t)
        sf, df = ev.src_flags, ev.dst_flags
        # who is who
        if is_me(sf, ev.src_guid) and ev.src_name:
            self.me_name = short_name(ev.src_name)
        for guid, flags, name in ((ev.src_guid, sf, ev.src_name), (ev.dst_guid, df, ev.dst_name)):
            if is_my_pet(flags, guid) and guid:
                if self._pet_alive is None and f is not None and ev.t > f.start:
                    f.pet_alive_s += ev.t - f.start  # first sighting: the pet was there since the pull
                if guid != self._pet_guid or (self._pet_alive is False and ev.event != "UNIT_DIED"):
                    self._pet_guid, self.pet_name = guid, short_name(name)
                    self._pet_alive = True
                if f is not None:
                    f.pet_seen = True
        if ev.event == "SPELL_SUMMON" and is_me(sf, ev.src_guid):
            self._pet_guid, self.pet_name, self._pet_alive = ev.dst_guid, short_name(ev.dst_name), True

        if ev.event == "ENVIRONMENTAL_DAMAGE":
            if is_me(df, ev.dst_guid):
                self.env_damage[ENV_FR.get(ev.env_type, ev.env_type.lower() or "environnement")] += ev.amount
            self._remember(ev, "")
        elif ev.is_damage and ev.amount >= 0:
            ours_src, ours_dst = is_friendly_side(sf), is_friendly_side(df)
            if (ours_src and is_hostile(df)) or (ours_dst and is_hostile(sf)):
                if f is None:
                    f = self.current = Fight(start=ev.t, end=ev.t)
                    f.pet_seen = bool(self._pet_alive)
                f.end = ev.t
                mob = ev.dst_guid if ours_src else ev.src_guid
                f.mobs.add(mob)
                self._engaged[mob] = ev.t
                for g, last in list(self._engaged.items()):
                    if ev.t - last > 8.0:
                        del self._engaged[g]
                f.max_adds = max(f.max_adds, len(self._engaged))
                self._damage(f, ev)
        elif ev.is_heal and f is not None and is_me(df, ev.dst_guid):
            f.heal_me += ev.amount
        elif ev.event in ("SPELL_AURA_BROKEN", "SPELL_AURA_BROKEN_SPELL") and f is not None and is_hostile(df):
            who = short_name(ev.src_name) or "?"
            by = f" ({ev.extra_spell_name})" if ev.extra_spell_name else ""
            f.cc_broken.append(f"{ev.spell_name or 'Contrôle'} cassé par {who}{by}.")
        elif ev.event in ("UNIT_DIED", "UNIT_DESTROYED"):
            self._death(f, ev)

        # HP tracking from the advanced block
        pct = ev.hp_pct
        if pct is not None and f is not None and ev.info_guid:
            if ev.info_guid == ev.dst_guid and is_me(df, ev.dst_guid) or \
                    ev.info_guid == ev.src_guid and is_me(sf, ev.src_guid):
                f.min_hp_me = pct if f.min_hp_me is None else min(f.min_hp_me, pct)
            elif ev.info_guid == self._pet_guid:
                f.min_hp_pet = pct if f.min_hp_pet is None else min(f.min_hp_pet, pct)
        return ended

    def _remember(self, ev: CombatEvent, src: str) -> None:
        q = self._recent.setdefault(ev.dst_guid, deque(maxlen=12))
        q.append(Hit(ev.t, src or ENV_FR.get(ev.env_type, ""), ev.spell_name or ("Mêlée" if ev.event.startswith("SWING") else ""),
                     ev.amount))
        if len(self._recent) > 300:
            self._recent.pop(next(iter(self._recent)))

    def _damage(self, f: Fight, ev: CombatEvent) -> None:
        sf, df = ev.src_flags, ev.dst_flags
        if is_me(sf, ev.src_guid):
            f.dmg_me += ev.amount
            f.spells[ev.spell_name or "Mêlée"] += ev.amount
            if f.biggest_hit is None or ev.amount > f.biggest_hit.amount:
                f.biggest_hit = Hit(ev.t, self.me_name, ev.spell_name or "Mêlée", ev.amount)
        elif is_my_pet(sf, ev.src_guid):
            f.dmg_pet += ev.amount
            f.pet_seen = True
            f.pet_active_bins.add(int((ev.t - f.start) // 2))
        elif is_party(sf):
            f.dmg_party[short_name(ev.src_name)] += ev.amount
        if is_hostile(sf):
            self._remember(ev, ev.src_name)
            if is_me(df, ev.dst_guid):
                f.taken_me += ev.amount
            elif is_my_pet(df, ev.dst_guid):
                f.taken_pet += ev.amount
            elif is_party(df) and ev.event.startswith("SWING"):
                f.aggro_friends[short_name(ev.dst_name)] += 1

    def _death(self, f: Fight | None, ev: CombatEvent) -> None:
        df = ev.dst_flags
        if is_hostile(df):
            if f is not None:
                f.kills += 1
            self._engaged.pop(ev.dst_guid, None)
            return
        if is_me(df, ev.dst_guid):
            who = "me"
        elif is_my_pet(df, ev.dst_guid):
            who = "pet"
            self._pet_alive = False
        elif is_party(df):
            who = "party"
        else:
            return
        hits = [h for h in self._recent.get(ev.dst_guid, ()) if ev.t - h.t <= DEATH_RECAP_S]
        last = hits[-1] if hits else None
        d = Death(ev.t, short_name(ev.dst_name) or ("Familier" if who == "pet" else "?"), who,
                  killer=last.source if last else "", spell=last.spell if last else "", recent=hits)
        if f is not None:
            f.deaths.append(d)
        else:
            self.deaths_out_of_fight.append(d)
        self._recent.pop(ev.dst_guid, None)

    # --- session level --------------------------------------------------------------------------
    def all_fights(self) -> list[Fight]:
        out = list(self.fights)
        if self.current is not None:
            out.append(self.current)
        return out

    def last_fight(self) -> Fight | None:
        return self.fights[-1] if self.fights else None

    def to_dict(self) -> dict[str, Any]:
        fights = self.all_fights()
        dur = sum(f.duration for f in fights)
        dmg = sum(f.dmg_me + f.dmg_pet for f in fights)
        deaths = [d for f in fights for d in f.deaths] + self.deaths_out_of_fight
        ups = [f.pet_uptime for f in fights if f.pet_uptime is not None]
        mistakes = Counter()
        for f in fights:
            for m in f.mistakes():
                mistakes[_mistake_kind(m)] += 1
        return {
            "fights": len(fights), "combat_time": round(dur, 1), "dps": round(dmg / dur, 1) if dur else 0.0,
            "damage": dmg, "kills": sum(f.kills for f in fights),
            "deaths_me": sum(1 for d in deaths if d.who == "me"),
            "deaths_pet": sum(1 for d in deaths if d.who == "pet"),
            "deaths_party": sum(1 for d in deaths if d.who == "party"),
            "pet_uptime": round(sum(ups) / len(ups), 3) if ups else None,
            "environment": dict(self.env_damage), "mistakes": dict(mistakes),
        }

    def text_fr(self) -> str:
        """Multi-line French session report."""
        d = self.to_dict()
        if not d["fights"]:
            return "Pas encore de combat dans le journal (tape /combatlog en jeu)."
        lines = [f"Session : {d['fights']} combat{'s' if d['fights'] > 1 else ''}, "
                 f"{_mmss(d['combat_time'])} en combat, {d['dps']:.0f} DPS moyen, {d['kills']} monstres tués."]
        morts = []
        if d["deaths_me"]:
            morts.append(f"toi {d['deaths_me']}")
        if d["deaths_pet"]:
            morts.append(f"familier {d['deaths_pet']}")
        if d["deaths_party"]:
            morts.append(f"groupe {d['deaths_party']}")
        if morts:
            lines.append("Morts : " + ", ".join(morts) + ".")
        if d["pet_uptime"] is not None:
            lines.append(f"Familier en vie {d['pet_uptime'] * 100:.0f} % du temps de combat.")
        for kind, dmg in self.env_damage.items():
            lines.append(f"Dégâts de {kind} : {_num(dmg)}.")
        best = max(self.all_fights(), key=lambda f: f.dps)
        lines.append(f"Meilleur combat : {best.dps:.0f} DPS.")
        last = self.all_fights()[-1]
        lines.append("Dernier combat : " + last.summary_fr())
        for m in last.mistakes()[:4]:
            lines.append(" - " + m)
        return "\n".join(lines)

    def facts_fr(self, max_len: int = 400) -> str:
        """Compact one-paragraph facts for the AI coach prompt (no player names except group friends)."""
        try:
            d = self.to_dict()
            if not d["fights"]:
                return ""
            s = (f"Journal de combat : {d['fights']} combats, {d['dps']:.0f} DPS moyen, morts toi {d['deaths_me']}"
                 f" / familier {d['deaths_pet']} / groupe {d['deaths_party']}.")
            last = self.all_fights()[-1]
            s += " Dernier : " + last.summary_fr()
            ms = last.mistakes()
            if ms:
                s += " Erreurs : " + " ".join(ms[:3])
            return s[:max_len]
        except Exception as e:
            log.debug("facts_fr: %s", e)
            return ""


def _mistake_kind(m: str) -> str:
    for key, kind in (("Familier mort", "familier mort"), ("Tu es mort", "mort"), ("vie est descendue", "vie basse"),
                      ("monstres engagés", "trop de monstres"), ("cassé", "contrôle cassé"),
                      ("aggro", "aggro groupe"), ("peu actif", "familier inactif")):
        if key in m:
            return kind
    return "autre"
