"""Live WoW voice callouts from the combat log, spoken through TreeAI's existing voice engine.

``WowVoiceCoach(say)`` where ``say(text, level)`` is :meth:`treeaicoach.voice.VoiceEngine.say`
(levels 0 INFO / 1 WARNING / 2 DANGER, as :class:`treeaicoach.alerts.Level`). Callouts (short French):
pet died / pet low, low HP (needs advanced logging), mob on a friend, adds (3+ mobs), big mob
(elite-like HP), drowning / fatigue / lava, dangerous enemy cast to interrupt, crowd control broken,
friend died. A noise budget (per-key cooldown + a token bucket for non-danger lines) keeps it calm.

:class:`WowCombatSession` glues the log tail, this coach and the session report (one thread).

Read-only: only the game's own log file is read; nothing is sent to the game (Blizzard ToS).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from treeaicoach.wow.combatlog import (CombatEvent, CombatLogWatcher, is_hostile, is_me, is_my_pet, is_party,
                                       short_name)
from treeaicoach.wow.report import Fight, SessionReport

log = logging.getLogger(__name__)

INFO, WARNING, DANGER = 0, 1, 2

# per-callout cooldowns (seconds of log time)
COOLDOWNS: dict[str, float] = {
    "pet_died": 20.0, "pet_low": 15.0, "me_low": 12.0, "friend_aggro": 12.0, "mob_on_me": 15.0,
    "adds": 20.0, "elite": 60.0, "env": 8.0, "cast": 6.0, "cc_broke": 8.0, "friend_died": 5.0,
}
# callouts that always pass the noise budget (still per-key cooldowns); the others share the budget
IMPORTANT = frozenset({"pet_died", "friend_died", "cast", "cc_broke", "me_low", "env"})
ME_LOW_PCT = 35.0
PET_LOW_PCT = 30.0
ADDS_N = 3
ADDS_WINDOW_S = 8.0
ELITE_HP_RATIO = 3.0

# dangerous enemy casts worth interrupting (FR + EN keywords, lowercase, accent-insensitive match)
_CAST_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("soin", "Il se soigne, coupe-le !"), ("heal", "Il se soigne, coupe-le !"),
    ("renovation", "Il se soigne, coupe-le !"), ("renew", "Il se soigne, coupe-le !"),
    ("guerison", "Il se soigne, coupe-le !"), ("priere", "Il se soigne, coupe-le !"),
    ("peur", "Peur en approche, coupe-le !"), ("fear", "Peur en approche, coupe-le !"),
    ("hurlement", "Peur en approche !"), ("metamorphose", "Mouton en approche, coupe-le !"),
    ("polymorph", "Mouton en approche, coupe-le !"), ("sommeil", "Sommeil en approche, coupe-le !"),
    ("sleep", "Sommeil en approche, coupe-le !"), ("charme", "Contrôle mental, coupe-le !"),
    ("domination", "Contrôle mental, coupe-le !"),
)
_ENV_TEXT = {
    "DROWNING": ("Tu te noies, remonte !", DANGER),
    "FATIGUE": ("Fatigue, fais demi-tour !", DANGER),
    "LAVA": ("Sors de la lave !", DANGER),
    "FIRE": ("Sors du feu !", WARNING),
    "SLIME": ("Sors de la vase !", WARNING),
}


def _fold(s: str) -> str:
    tr = str.maketrans("àâäéèêëîïôöùûüç", "aaaeeeeiioouuuc")
    return (s or "").lower().translate(tr)


@dataclass
class Callout:
    t: float
    key: str
    text: str
    level: int


class WowVoiceCoach:
    """Turns combat events into short spoken callouts. ``feed(events)`` returns what was said."""

    def __init__(self, say: Callable[..., Any] | None = None, *, budget: int = 4, budget_window: float = 20.0,
                 min_gap: float = 1.5, max_lag: float | None = 15.0, wall: Callable[[], float] = time.time,
                 enabled: dict[str, bool] | None = None, tank_name: str = "") -> None:
        self.say = say
        self.budget = max(1, int(budget))
        self.budget_window = float(budget_window)
        self.min_gap = float(min_gap)
        self.max_lag = max_lag
        self.wall = wall
        self.enabled = dict(enabled or {})
        self.tank_name = tank_name
        self.spoken: list[Callout] = []
        self.dropped = 0
        self._last_key: dict[str, float] = {}
        self._recent_lines: deque[float] = deque()
        self._last_line_t = -1e18
        self._engaged: dict[str, float] = {}
        self._adds_called = 0
        self._my_max_hp = 0
        self._pet_guid = ""
        self._pet_dead = False
        self._elite_seen: set[str] = set()
        self._party_hits: dict[str, deque[float]] = {}
        self._me_hits: deque[float] = deque()
        self._me_low = False
        self._pet_low = False

    # --- speaking -------------------------------------------------------------------------------
    def _speak(self, t: float, key: str, sub: str, text: str, level: int, live: bool) -> bool:
        family = key
        if self.enabled.get(family) is False:
            return False
        k = f"{key}:{sub}" if sub else key
        if t - self._last_key.get(k, -1e18) < COOLDOWNS.get(family, 10.0):
            return False
        if level < DANGER and family not in IMPORTANT:
            while self._recent_lines and t - self._recent_lines[0] > self.budget_window:
                self._recent_lines.popleft()
            if len(self._recent_lines) >= self.budget or t - self._last_line_t < self.min_gap:
                self.dropped += 1
                return False
            self._recent_lines.append(t)
        self._last_key[k] = t
        self._last_line_t = max(self._last_line_t, t)
        self.spoken.append(Callout(t, key, text, level))
        if len(self.spoken) > 200:
            del self.spoken[:100]
        if live and self.say is not None:
            try:
                self.say(text, level)
            except Exception as e:
                log.debug("wow coach: say failed: %s", e)
        return True

    # --- events ---------------------------------------------------------------------------------
    def feed(self, events: Iterable[CombatEvent]) -> list[Callout]:
        n0 = len(self.spoken)
        for ev in events:
            try:
                live = self.max_lag is None or abs(self.wall() - ev.t) <= self.max_lag
                self._one(ev, live)
            except Exception as e:
                log.debug("wow coach: event skipped (%s)", e)
        return self.spoken[n0:]

    def _one(self, ev: CombatEvent, live: bool) -> None:
        t, sf, df, name = ev.t, ev.src_flags, ev.dst_flags, ev.event
        for guid, flags in ((ev.src_guid, sf), (ev.dst_guid, df)):
            if guid and is_my_pet(flags, guid) and guid != self._pet_guid:
                self._pet_guid, self._pet_dead, self._pet_low = guid, False, False
            elif guid and guid == self._pet_guid and self._pet_dead and name != "UNIT_DIED":
                self._pet_dead = False

        # HP from the advanced block
        pct = ev.hp_pct
        if pct is not None and ev.info_guid:
            if (ev.info_guid == ev.dst_guid and is_me(df, ev.dst_guid)) or \
                    (ev.info_guid == ev.src_guid and is_me(sf, ev.src_guid)):
                self._my_max_hp = ev.max_hp
                if pct < ME_LOW_PCT and not self._me_low:
                    if self._speak(t, "me_low", "", f"Vie basse, {pct:.0f} % !", DANGER, live):
                        self._me_low = True
                elif pct > ME_LOW_PCT + 15:
                    self._me_low = False
            elif ev.info_guid == self._pet_guid and self._pet_guid and not self._pet_dead:
                if 0 < pct < PET_LOW_PCT and not self._pet_low:
                    if self._speak(t, "pet_low", "", f"Familier à {pct:.0f} %, soigne-le.", WARNING, live):
                        self._pet_low = True
                elif pct > PET_LOW_PCT + 20:
                    self._pet_low = False
            elif ev.info_guid not in self._elite_seen and (
                    (ev.info_guid == ev.dst_guid and is_hostile(df)) or (ev.info_guid == ev.src_guid and is_hostile(sf))):
                # advanced info describes the target or (swings) the attacker: both handled
                if self._my_max_hp and ev.max_hp >= ELITE_HP_RATIO * self._my_max_hp:
                    self._elite_seen.add(ev.info_guid)
                    self._speak(t, "elite", ev.info_guid, "Gros monstre, probablement élite. Prudence.", WARNING, live)

        if name == "ENVIRONMENTAL_DAMAGE":
            txt = _ENV_TEXT.get(ev.env_type)
            if txt and is_me(df, ev.dst_guid):
                self._speak(t, "env", ev.env_type, txt[0], txt[1], live)
        elif ev.is_damage:
            self._damage(ev, live)
        elif name == "SPELL_CAST_START" and is_hostile(sf):
            folded = _fold(ev.spell_name)
            for kw, txt in _CAST_KEYWORDS:
                if kw in folded:
                    self._speak(t, "cast", str(ev.spell_id or folded), txt, WARNING, live)
                    break
        elif name in ("SPELL_AURA_BROKEN", "SPELL_AURA_BROKEN_SPELL") and is_hostile(df):
            who = short_name(ev.src_name)
            what = ev.spell_name or "Contrôle"
            txt = f"{what} cassé" + (f" par {who}." if who and not is_me(sf, ev.src_guid) else ".")
            self._speak(t, "cc_broke", ev.dst_guid, txt, WARNING, live)
        elif name == "UNIT_DIED":
            if is_my_pet(df, ev.dst_guid):
                self._pet_dead = True
                self._speak(t, "pet_died", "", "Ton familier est mort !", WARNING, live)
            elif is_party(df):
                who = short_name(ev.dst_name)
                self._speak(t, "friend_died", who, f"{who} est mort.", WARNING, live)
            elif is_hostile(df):
                self._engaged.pop(ev.dst_guid, None)
            elif is_me(df, ev.dst_guid):
                self._engaged.clear()
                self._adds_called = 0

    def _damage(self, ev: CombatEvent, live: bool) -> None:
        t, sf, df = ev.t, ev.src_flags, ev.dst_flags
        ours_src = bool(sf & 0x7)
        ours_dst = bool(df & 0x7)
        if not ((ours_src and is_hostile(df)) or (ours_dst and is_hostile(sf))):
            return
        mob = ev.dst_guid if ours_src else ev.src_guid
        self._engaged[mob] = t
        for g, last in list(self._engaged.items()):
            if t - last > ADDS_WINDOW_S:
                del self._engaged[g]
        n = len(self._engaged)
        if n < ADDS_N:
            self._adds_called = 0
        elif n > self._adds_called:
            if self._speak(t, "adds", "" if self._adds_called == 0 else str(n), f"Attention, {n} monstres !",
                           WARNING, live):
                self._adds_called = n
        if not is_hostile(sf) or not ev.event.startswith("SWING"):
            return
        if is_party(df):
            who = short_name(ev.dst_name)
            q = self._party_hits.setdefault(who, deque(maxlen=64))
            q.append(t)
            if len(q) >= 2 and t - q[-2] <= 3.0 and who != self._tank(t):
                self._speak(t, "friend_aggro", who, f"Aggro sur {who} !", WARNING, live)
        elif is_me(df, ev.dst_guid) and self._pet_guid and not self._pet_dead:
            self._me_hits.append(t)
            while self._me_hits and t - self._me_hits[0] > 3.0:
                self._me_hits.popleft()
            if len(self._me_hits) >= 2:
                self._speak(t, "mob_on_me", "", "Le monstre est sur toi, renvoie le familier !", WARNING, live)

    def _tank(self, t: float) -> str:
        if self.tank_name:
            return self.tank_name
        best, best_n = "", 0
        for who, q in self._party_hits.items():
            n = sum(1 for x in q if t - x <= 60.0)
            if n > best_n:
                best, best_n = who, n
        return best if best_n >= 6 else ""


class WowCombatSession:
    """Owns the log watcher, the voice coach and the session report. Thread-safe getters."""

    def __init__(self, log_path: str | Path, say: Callable[..., Any] | None = None,
                 on_fight_end: Callable[[Fight], None] | None = None, *, voice: bool = True,
                 interval: float = 0.25, **coach_kw: Any) -> None:
        self.coach = WowVoiceCoach(say if voice else None, **coach_kw)
        self.report = SessionReport()
        self.on_fight_end = on_fight_end
        self._lock = threading.Lock()
        self.wall = coach_kw.get("wall", time.time)
        self.watcher = CombatLogWatcher(log_path, self._on_events, interval=interval, idle_calls=True)

    def start(self) -> None:
        self.watcher.start()

    def stop(self) -> None:
        self.watcher.stop()

    @property
    def running(self) -> bool:
        return self.watcher.running

    def _on_events(self, events: list[CombatEvent]) -> None:
        try:
            if events:
                self.coach.feed(events)
            with self._lock:
                ended = self.report.feed(events)
                f = self.report.flush(self.wall())
                if f is not None:
                    ended.append(f)
            for f in ended:
                if self.on_fight_end is not None:
                    try:
                        self.on_fight_end(f)
                    except Exception as e:
                        log.debug("wow session: on_fight_end failed: %s", e)
        except Exception as e:
            log.debug("wow session: %s", e)

    def report_text(self) -> str:
        with self._lock:
            return self.report.text_fr()

    def report_dict(self) -> dict[str, Any]:
        with self._lock:
            return self.report.to_dict()

    def facts(self) -> str:
        with self._lock:
            return self.report.facts_fr()
