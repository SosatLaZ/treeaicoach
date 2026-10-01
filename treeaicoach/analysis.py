"""Post-game analysis of a game record (``recorder.py`` format) and the live death recap.

:func:`analyze_game` is pure (no I/O) and never raises: every section is computed on its
own, so a partial or damaged record still yields whatever can be computed (problems are
listed in ``result["errors"]``). It produces (ARCHITECTURE.md §6.5):

* ``summary``: champion, duration, K/D/A, CS/min, vision/min, final level, kill participation;
* ``deaths``: each of my deaths with its context (zone, killer and assisters mapped to
  champions, enemies seen < 0.2 from me in the 8 s before, enemy jungler involved?, gank alert
  given in the 12 s before? -> "alerte ignorée" / "mort sans alerte");
* ``ganks``: DANGER gank alerts grouped into episodes and their outcome in the next 15 s;
* ``jungler``: enemy jungler first sighting, appearances per zone and phase (0-10 / 10-20 /
  20+ min), lanes where he took part in kills;
* ``zones``: my time per zone; ``objectives`` per team; ``tips``: 3-8 French tips with numbers.

v2 coaching sections:

* ``phases``: laning (0-14 min) / mid game (14-25) / late game (25+) breakdown: deaths, K/A,
  CS/min, vision/min, time in lane, gank exposure, ganks, epic objectives;
* ``presence``: my zone distribution per phase vs the ideal one for my role, with a 0-100 match;
* ``exposure``: gank exposure score (share of my visible time spent past the middle of my lane,
  or in the enemy jungle, while the enemy jungler was unseen for 30 s or more);
* ``objective_presence``: was I near the pit when each epic monster was taken?
* ``trends``: per-minute CS/min, vision score and kill participation from the snapshots;
* ``pathing``: enemy jungler pathing summary (first side seen, path of his appearances,
  lanes he ganked); each death also gets ``jungler_unseen_s``;
* ``spoken_summary(analysis)``: a short French end-of-game summary for the voice.

:func:`death_recap` builds the short sentence spoken ~2 s after my death.
Only the standard library and ``treeaicoach.geometry`` are used.
"""

from __future__ import annotations

import bisect
import logging
import math
import re
import unicodedata
from typing import Any, Iterable

from treeaicoach import geometry
from treeaicoach.geometry import Zone

log = logging.getLogger(__name__)

# Alert kinds (values of alerts.AlertKind, as stored in records)
GANK_KINDS: frozenset[str] = frozenset({"jungler_approach", "roam_approach", "collapse"})
NON_THREAT_KINDS: frozenset[str] = frozenset({"objective_soon", "recall_gold", "control_ward",
                                              "jungler_where", "death_recap", "macro_tip"})
LEVEL_WARNING = 1
LEVEL_DANGER = 2

NEAR_RADIUS = 0.2            # enemy "near" my death (normalized minimap distance)
NEAR_WINDOW_S = 8.0          # ... seen in the 8 s before the death
WARN_WINDOW_S = 12.0         # gank alert in the 12 s before the death -> "alerte ignorée"
GANK_OUTCOME_S = 15.0        # death within 15 s after a DANGER alert -> gank "mort"
GANK_MERGE_S = 10.0          # DANGER alerts closer than this belong to the same gank episode
DEATH_POS_WINDOW_S = 6.0     # my last position at most 6 s before the death
MY_POS_MATCH_S = 2.0         # my position sample matched with an enemy sighting (+-2 s)
KILL_POS_WINDOW_S = 6.0      # enemy jungler position around a kill (+-6 s)
APPEARANCE_GAP_S = 5.0       # hidden longer than this = a new appearance
MAX_SAMPLE_DT = 2.0          # a position sample counts for at most 2 s of presence
PHASES: tuple[tuple[str, float, float], ...] = (("0-10", 0.0, 600.0), ("10-20", 600.0, 1200.0),
                                                ("20+", 1200.0, math.inf))
PHASE_LABELS_FR = {"0-10": "0–10 min", "10-20": "10–20 min", "20+": "20 min et +"}
RECAP_MAX_WORDS = 20
MIN_TIPS, MAX_TIPS = 3, 8
JUNGLER_UNSEEN_RISK_S = 30.0  # enemy jungler unseen this long = he can be anywhere
#: Game phases of the v2 breakdown (id, French label, start, end).
GAME_PHASES: tuple[tuple[str, str, float, float], ...] = (
    ("laning", "Phase de voie", 0.0, 840.0), ("mid", "Milieu de partie", 840.0, 1500.0),
    ("late", "Fin de partie", 1500.0, math.inf))
EXPOSED_LANE_S = 0.55        # past this fraction of my lane (from my base) = exposed to ganks
OBJ_NEAR_R = 0.22            # "near the pit" when an epic monster dies
OBJ_TIME_TOL_S = 10.0

LANE_ROLE = {"TOP": "top", "MIDDLE": "mid", "BOTTOM": "bot", "UTILITY": "bot"}
LANE_FR = {"top": "en haut", "mid": "au milieu", "bot": "en bas", "jungle": "en jungle"}
ROLE_FR = {"TOP": "Haut", "JUNGLE": "Jungle", "MIDDLE": "Milieu", "BOTTOM": "Tireur", "UTILITY": "Support"}
RESULT_FR = {"Win": "Victoire", "Lose": "Défaite"}


# ======================================================================================
# small helpers
# ======================================================================================
def _finite(x: Any, default: float | None = None) -> float | None:
    if x is None or isinstance(x, bool):
        return default
    try:
        f = float(x)
    except (TypeError, ValueError, OverflowError):
        return default
    return f if math.isfinite(f) else default


def _int(x: Any, default: int = 0) -> int:
    f = _finite(x)
    return int(f) if f is not None else default


def _dict(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _list(x: Any) -> list:
    return x if isinstance(x, list) else []


def _str(x: Any) -> str:
    if x is None or isinstance(x, (dict, list)):
        return ""
    try:
        return " ".join(str(x).split())
    except Exception:
        return ""


def norm_name(x: Any) -> str:
    """Comparable player / champion name: NFC, case-folded, single spaces."""
    s = _str(x)
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s).replace(" ", " ").replace("​", "")
    return " ".join(s.split()).casefold()


def alnum_name(x: Any) -> str:
    """Looser key: accents removed, letters and digits only ("Kai'Sa" -> "kaisa")."""
    s = unicodedata.normalize("NFKD", _str(x))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return "".join(ch for ch in s.casefold() if ch.isalnum())


def fmt_time(gt: Any) -> str:
    """Game time as ``m:ss`` (``"4:07"``, ``"28:14"``); ``"?"`` if invalid."""
    f = _finite(gt)
    if f is None or f < 0:
        return "?"
    s = int(round(f))
    return f"{s // 60}:{s % 60:02d}"


def fmt_num(x: Any, decimals: int = 1) -> str:
    """French decimal number (``5,8``; thousands separated by a narrow no-break space)."""
    f = _finite(x)
    if f is None:
        return "?"
    s = f"{f:,.{decimals}f}"
    return s.replace(",", " ").replace(".", ",")


def fmt_target(x: float) -> str:
    """Target number without a useless decimal (``7`` / ``7,5``)."""
    return fmt_num(x, 0) if float(x).is_integer() else fmt_num(x, 1)


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if abs(n) <= 1 else (plural or word + 's')}"


def phase_of(gt: float) -> str:
    """``"0-10"`` / ``"10-20"`` / ``"20+"``."""
    for name, a, b in PHASES:
        if a <= gt < b:
            return name
    return PHASES[0][0] if gt < 0 else PHASES[-1][0]


def _zone_value(u: float, v: float) -> str:
    return geometry.classify_zone(u, v).value


def _zone_name(zone: Any, my_team: str | None) -> str:
    try:
        return geometry.zone_name_fr(zone, my_team) or "zone inconnue"
    except Exception:
        return "zone inconnue"


def _kind(x: Any) -> str:
    s = _str(getattr(x, "value", x)).lower()
    return s


# ======================================================================================
# normalized record
# ======================================================================================
class _Series:
    """Time-sorted ``(game_time, u, v)`` samples with a parallel list of times (bisect)."""

    __slots__ = ("pts", "times")

    def __init__(self, raw: Any) -> None:
        pts: list[tuple[float, float, float]] = []
        for p in _list(raw):
            try:
                gt, u, v = _finite(p[0]), _finite(p[1]), _finite(p[2])
            except (TypeError, IndexError, KeyError):
                continue
            if gt is None or u is None or v is None or gt < 0:
                continue
            pts.append((gt, min(1.0, max(0.0, u)), min(1.0, max(0.0, v))))
        pts.sort(key=lambda p: p[0])
        self.pts = pts
        self.times = [p[0] for p in pts]

    def __len__(self) -> int:
        return len(self.pts)

    def window(self, t0: float, t1: float) -> list[tuple[float, float, float]]:
        i = bisect.bisect_left(self.times, t0)
        j = bisect.bisect_right(self.times, t1)
        return self.pts[i:j]

    def nearest(self, t: float, tol: float) -> tuple[float, float, float] | None:
        if not self.pts:
            return None
        i = bisect.bisect_left(self.times, t)
        best = None
        for k in (i - 1, i):
            if 0 <= k < len(self.pts):
                d = abs(self.pts[k][0] - t)
                if d <= tol and (best is None or d < abs(best[0] - t)):
                    best = self.pts[k]
        return best

    def last_before(self, t: float, window: float, after: float = 0.5) -> tuple[float, float, float] | None:
        i = bisect.bisect_right(self.times, t + after) - 1
        if i >= 0 and self.pts[i][0] >= t - window:
            return self.pts[i]
        return None


class _Actor(dict):
    """Description of a killer / assister (dict: label, alias, team, kind, is_jungler, is_me)."""


class _Rec:
    """Defensive, normalized view of a record dict."""

    def __init__(self, record: Any) -> None:
        r = record if isinstance(record, dict) else {}
        self.raw = r
        self.meta = _dict(r.get("meta"))
        self.roster = [p for p in _list(r.get("roster")) if isinstance(p, dict)]
        snaps = []
        for s in _list(r.get("snapshots")):
            if isinstance(s, dict) and _finite(s.get("game_time")) is not None:
                snaps.append(s)
        snaps.sort(key=lambda s: _finite(s.get("game_time"), 0.0))
        self.snapshots = snaps
        self.my_pos = _Series(r.get("my_positions"))
        self.sightings: dict[str, _Series] = {}
        for k, v in _dict(r.get("sightings")).items():
            ser = _Series(v)
            if len(ser):
                self.sightings[_str(k)] = ser
        self.alerts = self._parse_alerts(r.get("alerts"))
        self.events = [e for e in _list(r.get("events")) if isinstance(e, dict) and e.get("EventName")]
        self.events.sort(key=lambda e: _finite(e.get("EventTime"), 0.0))
        res = _str(r.get("result"))
        self.result = res if res in ("Win", "Lose") else None
        if self.result is None:
            for e in reversed(self.events):
                if e.get("EventName") == "GameEnd":
                    rr = _str(e.get("Result")).lower()
                    self.result = "Win" if rr == "win" else "Lose" if rr == "lose" else None
                    break
        self.me = self._find_me()
        team = _str(self.meta.get("team")).upper() or _str(self.me.get("team")).upper()
        self.my_team: str | None = team if team in ("ORDER", "CHAOS") else None
        self.enemy_team: str | None = None
        if self.my_team:
            self.enemy_team = "CHAOS" if self.my_team == "ORDER" else "ORDER"
        self.jungler = self._find_enemy_jungler()
        self._build_name_index()
        self.duration = self._duration(r)

    # ---------------------------------------------------------------- parsing
    @staticmethod
    def _parse_alerts(raw: Any) -> list[tuple[float, str, int, str, str | None]]:
        out = []
        for a in _list(raw):
            try:
                if isinstance(a, dict):
                    gt = _finite(a.get("game_time", a.get("t")))
                    kind, lvl, text, alias = a.get("kind"), a.get("level"), a.get("text"), a.get("alias")
                else:
                    gt, kind, lvl, text = _finite(a[0]), a[1], a[2], a[3] if len(a) > 3 else ""
                    alias = a[4] if len(a) > 4 else None
            except (TypeError, IndexError, KeyError):
                continue
            if gt is None or gt < 0:
                continue
            lv = _finite(lvl)
            if lv is None:
                lv = {"info": 0, "warning": 1, "danger": 2}.get(_str(lvl).lower(), 0)
            out.append((gt, _kind(kind), int(max(0, min(2, lv))), _str(text), _str(alias) or None))
        out.sort(key=lambda a: a[0])
        return out

    def _find_me(self) -> dict:
        for p in self.roster:
            if p.get("is_me") is True:
                return p
        rid, summ = norm_name(self.meta.get("riot_id")), norm_name(self.meta.get("summoner_name"))
        for p in self.roster:
            if (rid and norm_name(p.get("riot_id")) == rid) or (summ and norm_name(p.get("summoner_name")) == summ):
                return p
        champ = alnum_name(self.meta.get("champion"))
        team = _str(self.meta.get("team")).upper()
        for p in self.roster:
            if champ and alnum_name(p.get("alias")) == champ and (not team or _str(p.get("team")).upper() == team):
                return p
        return {"alias": self.meta.get("champion", ""), "name": self.meta.get("champion_name", ""),
                "team": self.meta.get("team", ""), "position": self.meta.get("position", ""),
                "riot_id": self.meta.get("riot_id", ""), "summoner_name": self.meta.get("summoner_name", ""),
                "is_me": True}

    def _find_enemy_jungler(self) -> dict | None:
        enemies = [p for p in self.roster if self.enemy_team and _str(p.get("team")).upper() == self.enemy_team]
        smiters = [p for p in enemies if p.get("has_smite") is True]
        for p in smiters:
            if _str(p.get("position")).upper() == "JUNGLE":
                return p
        if smiters:
            return smiters[0]
        for p in enemies:
            if _str(p.get("position")).upper() == "JUNGLE":
                return p
        return None

    def _build_name_index(self) -> None:
        """Player name -> roster entry, by decreasing confidence tiers."""
        self._by_exact: dict[str, dict] = {}
        self._by_loose: dict[str, dict] = {}
        self._by_champ: dict[str, dict] = {}
        self._by_alias: dict[str, dict] = {}
        for p in self.roster:
            for key in (p.get("riot_id"), p.get("summoner_name")):
                n = norm_name(key)
                if n:
                    self._by_exact.setdefault(n, p)
                    self._by_exact.setdefault(n.split("#", 1)[0].strip(), p)
                    self._by_loose.setdefault(alnum_name(n.split("#", 1)[0]), p)
            for key in (p.get("alias"), p.get("name")):
                a = alnum_name(key)
                if a:
                    self._by_champ.setdefault(a, p)
            a = alnum_name(p.get("alias"))
            if a:
                self._by_alias[a] = p

    @staticmethod
    def _duration(r: dict) -> float:
        d = _finite(r.get("duration"), 0.0) or 0.0
        return max(0.0, d)

    def finalize_duration(self) -> None:
        """Duration = max of the recorded duration and of every timestamp seen."""
        d = self.duration
        if self.snapshots:
            d = max(d, _finite(self.snapshots[-1].get("game_time"), 0.0) or 0.0)
        if self.my_pos.pts:
            d = max(d, self.my_pos.pts[-1][0])
        for s in self.sightings.values():
            d = max(d, s.pts[-1][0])
        if self.alerts:
            d = max(d, self.alerts[-1][0])
        if self.events:
            d = max(d, _finite(self.events[-1].get("EventTime"), 0.0) or 0.0)
        self.duration = d

    # ---------------------------------------------------------------- lookups
    def player(self, name: Any) -> dict | None:
        """Roster entry for a Live Client player name (riot id / summoner name / champion)."""
        n = norm_name(name)
        if not n:
            return None
        p = self._by_exact.get(n) or self._by_exact.get(n.split("#", 1)[0].strip())
        if p is not None:
            return p
        a = alnum_name(n.split("#", 1)[0])
        return self._by_loose.get(a) or self._by_champ.get(a)

    def by_alias(self, alias: Any) -> dict | None:
        return self._by_alias.get(alnum_name(alias)) or self._by_champ.get(alnum_name(alias))

    def is_me(self, name: Any) -> bool:
        p = self.player(name)
        if p is not None:
            return p is self.me or p.get("is_me") is True
        n = norm_name(name)
        if not n:
            return False
        for key in (self.meta.get("riot_id"), self.meta.get("summoner_name")):
            k = norm_name(key)
            if k and (n == k or n == k.split("#", 1)[0].strip()):
                return True
        champ = alnum_name(self.meta.get("champion_name")) or alnum_name(self.meta.get("champion"))
        return bool(champ) and alnum_name(n) == champ

    def display(self, alias_or_key: Any) -> str:
        """Champion display name for a sighting key / alias (anonymous -> "ennemi inconnu")."""
        s = _str(alias_or_key)
        if not s or "?" in s:
            return "ennemi inconnu"
        p = self.by_alias(s)
        if p is not None:
            return _str(p.get("name")) or _str(p.get("alias")) or s
        return s

    def is_jungler_key(self, key: Any) -> bool:
        if self.jungler is None:
            return False
        k = alnum_name(key)
        return bool(k) and k in (alnum_name(self.jungler.get("alias")), alnum_name(self.jungler.get("name")))

    def jungler_series(self) -> _Series | None:
        if self.jungler is None:
            return None
        for k, ser in self.sightings.items():
            if self.is_jungler_key(k):
                return ser
        return None

    def actor(self, name: Any) -> _Actor:
        """Describe a KillerName / assister name."""
        raw = _str(name)
        p = self.player(raw)
        if p is not None:
            is_me = p is self.me or p.get("is_me") is True
            return _Actor(label=_str(p.get("name")) or _str(p.get("alias")) or raw, alias=_str(p.get("alias")),
                          team=_str(p.get("team")).upper() or None, kind="champion",
                          is_jungler=self.jungler is not None and p is self.jungler, is_me=is_me)
        return _Actor(label=non_player_label(raw), alias=None, team=None, kind=non_player_kind(raw),
                      is_jungler=False, is_me=False)

    def is_enemy_actor(self, a: _Actor) -> bool:
        if a.get("kind") != "champion" or a.get("is_me"):
            return False
        if self.my_team is None or a.get("team") is None:
            return True
        return a.get("team") != self.my_team

    def gank_alerts(self, t0: float, t1: float) -> list[tuple[float, str, int, str, str | None]]:
        return [a for a in self.alerts if t0 <= a[0] <= t1 and a[1] in GANK_KINDS]

    def last_snapshot(self) -> dict:
        return self.snapshots[-1] if self.snapshots else {}


def non_player_kind(name: str) -> str:
    n = name.lower()
    if n.startswith("turret") or "turret" in n:
        return "turret"
    if "minion" in n:
        return "minion"
    if n.startswith("sru_") or "baron" in n or "dragon" in n or "herald" in n or "horde" in n:
        return "monster"
    return "unknown"


def non_player_label(name: str) -> str:
    """French label of a non-champion killer (``"Turret_T2_L_03_A"`` -> ``"Tourelle"``)."""
    n = name.lower()
    if "turret" in n:
        return "Tourelle"
    if "minion" in n:
        return "Sbires"
    for key, label in (("baron", "Baron Nashor"), ("elder", "Dragon ancestral"), ("dragon", "Dragon"),
                       ("riftherald", "Héraut"), ("herald", "Héraut"), ("horde", "Larves du Néant"),
                       ("atakhan", "Atakhan"), ("sru_", "Monstre")):
        if key in n:
            return label
    if "fountain" in n or "obelisk" in n:
        return "Fontaine"
    return name[:24] if name else "Inconnu"


# ======================================================================================
# deaths
# ======================================================================================
def _event_time(ev: Any) -> float | None:
    if isinstance(ev, dict):
        for k in ("EventTime", "game_time", "t"):
            f = _finite(ev.get(k))
            if f is not None:
                return f
        return None
    return _finite(ev)


def _my_death_events(rec: _Rec) -> list[dict]:
    return [e for e in rec.events if e.get("EventName") == "ChampionKill" and rec.is_me(e.get("VictimName"))]


def _deaths_from_snapshots(rec: _Rec) -> list[dict]:
    """Fallback when no ChampionKill event names me: death counter / is_dead transitions."""
    out: list[dict] = []
    prev_deaths: int | None = None
    prev_dead = False
    for s in rec.snapshots:
        gt = _finite(s.get("game_time"), 0.0) or 0.0
        d = _int(s.get("deaths"), 0)
        dead = s.get("is_dead") is True
        if prev_deaths is not None and (d > prev_deaths or (dead and not prev_dead)):
            n_new = max(1, d - prev_deaths) if d > prev_deaths else 1
            for _ in range(min(n_new, 5)):
                out.append({"EventName": "ChampionKill", "EventTime": gt, "_approx": True})
        elif prev_deaths is None and dead:
            out.append({"EventName": "ChampionKill", "EventTime": gt, "_approx": True})
        prev_deaths, prev_dead = d, dead
    # a death seen both as is_dead and counter increase in two snapshots: merge closer than 5 s
    merged: list[dict] = []
    for e in out:
        if merged and e["EventTime"] - merged[-1]["EventTime"] < 5.0:
            continue
        merged.append(e)
    return merged


def _death_context(rec: _Rec, event: Any) -> dict[str, Any] | None:
    """Everything known about one of my deaths (see module docstring)."""
    T = _event_time(event)
    if T is None:
        return None
    ev = event if isinstance(event, dict) else {}
    killer = rec.actor(ev.get("KillerName")) if _str(ev.get("KillerName")) else None
    assisters = [rec.actor(a) for a in _list(ev.get("Assisters")) if _str(a)]
    participants: list[str] = []
    for a in ([killer] if killer else []) + assisters:
        if rec.is_enemy_actor(a):
            al = a.get("alias") or a.get("label")
            if al and al not in participants:
                participants.append(al)
    pos_s = rec.my_pos.last_before(T, DEATH_POS_WINDOW_S)
    pos = (pos_s[1], pos_s[2]) if pos_s else None
    near: list[str] = []
    visible_any = False
    for key, ser in rec.sightings.items():
        for gt, u, v in ser.window(T - NEAR_WINDOW_S, T + 0.5):
            visible_any = True
            refs = []
            mine = rec.my_pos.nearest(gt, MY_POS_MATCH_S)
            if mine is not None:
                refs.append((mine[1], mine[2]))
            if pos is not None:
                refs.append(pos)
            if refs and min(geometry.dist((u, v), r) for r in refs) < NEAR_RADIUS:
                if key not in near:
                    near.append(key)
                break
    named_near = [k for k in near if "?" not in k]
    # enemies involved: participants (from the kill event) + named enemies seen near me
    involved: list[str] = list(participants)
    known = {alnum_name(x) for x in involved}
    for k in named_near:
        disp = rec.display(k)
        if alnum_name(k) not in known and alnum_name(disp) not in known:
            involved.append(k)
            known.add(alnum_name(k))
    anon_near = len(near) - len(named_near)
    enemy_count = len(involved) + (1 if anon_near and not involved else 0)
    jungler_involved = any(rec.is_jungler_key(x) for x in involved) or bool(
        killer and killer.get("is_jungler")) or any(a.get("is_jungler") for a in assisters)
    warn = rec.gank_alerts(T - WARN_WINDOW_S, T + 0.5)
    last_alert = warn[-1] if warn else None
    zone = _zone_value(*pos) if pos else None
    ctx: dict[str, Any] = {
        "game_time": round(T, 1),
        "time": fmt_time(T),
        "uv": [round(pos[0], 3), round(pos[1], 3)] if pos else None,
        "zone": zone,
        "zone_label": _zone_name(zone, rec.my_team) if zone else "zone inconnue",
        "killer": dict(killer) if killer else None,
        "assisters": [dict(a) for a in assisters],
        "participants": participants,
        "nearby": near,
        "visible_any": visible_any,
        "involved": involved,
        "involved_names": [rec.display(x) for x in involved],
        "enemy_count": enemy_count,
        "jungler_involved": bool(jungler_involved),
        "warned": last_alert is not None,
        "alert_before_s": round(T - last_alert[0], 1) if last_alert else None,
        "alert_kind": last_alert[1] if last_alert else None,
        "alert_text": last_alert[3] if last_alert else None,
        "verdict": "alerte ignorée" if last_alert else "mort sans alerte",
        "source": "snapshot" if ev.get("_approx") else "event",
    }
    jser = rec.jungler_series()
    unseen = None
    if rec.jungler is not None:
        last = None
        if jser is not None and len(jser):
            i = bisect.bisect_right(jser.times, T - 1.0) - 1
            if i >= 0:
                last = jser.pts[i][0]
        unseen = round(T - last, 1) if last is not None else (round(T - 90.0, 1) if T > 90.0 else None)
    ctx["jungler_unseen_s"] = unseen
    ctx["jungler_unseen"] = unseen is not None and unseen >= JUNGLER_UNSEEN_RISK_S
    ctx["recap"] = _recap_sentence(ctx, rec)
    return ctx


def _names_fr(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " et " + names[-1]


def _recap_sentence(ctx: dict[str, Any], rec: _Rec) -> str:
    """<= 20 words, e.g. "Mort face à 2 ennemis, dont le jungler. L'alerte avait été donnée 5 secondes avant." """
    n = int(ctx.get("enemy_count") or 0)
    killer = ctx.get("killer") or {}
    jungler = bool(ctx.get("jungler_involved"))
    names = ctx.get("involved_names") or []
    if n == 0 and killer.get("kind") == "turret":
        first = "Mort sous une tourelle ennemie."
    elif n == 0 and killer.get("kind") == "monster":
        first = f"Tué par {killer.get('label', 'un monstre')}."
    elif n == 1:
        who = names[0] if names else "un ennemi"
        first = f"Mort face à {who}, le jungler." if jungler and names else f"Mort face à {who}."
    elif n >= 2:
        first = f"Mort face à {n} ennemis, dont le jungler." if jungler else f"Mort face à {n} ennemis."
    else:
        first = ""
    if ctx.get("warned"):
        s = max(1, int(round(ctx.get("alert_before_s") or 0)))
        second = f"L'alerte avait été donnée {_plural(s, 'seconde')} avant."
    elif not ctx.get("visible_any"):
        second = "Aucune alerte : personne n'était visible sur la minimap."
        if not first:
            return "Mort sans alerte : personne n'était visible sur la minimap."
    elif not ctx.get("nearby"):
        second = "Aucune alerte : aucun ennemi visible près de toi."
    else:
        near_named = [rec.display(k) for k in ctx.get("nearby", []) if "?" not in k]
        if len(near_named) == 1:
            second = f"Aucune alerte, mais {near_named[0]} était visible près de toi."
        else:
            k = max(len(ctx.get("nearby", [])), 1)
            second = (f"Aucune alerte, mais {k} ennemis étaient visibles près de toi." if k >= 2
                      else "Aucune alerte, mais un ennemi était visible près de toi.")
    if not first:
        first = "Tu es mort."
    text = f"{first} {second}"
    if len(text.split()) > RECAP_MAX_WORDS:
        text = first if len(first.split()) <= RECAP_MAX_WORDS else " ".join(first.split()[:RECAP_MAX_WORDS])
    return text


def death_recap(record_so_far: Any, death_event: Any) -> str | None:
    """Short French sentence (<= 20 words) about my death, or None if it is not mine / on error.

    ``death_event`` is the Live Client ``ChampionKill`` event (or a dict with ``game_time``,
    or a plain game time). Never raises.
    """
    try:
        rec = _Rec(record_so_far)
        if isinstance(death_event, dict):
            victim = death_event.get("VictimName")
            if _str(victim) and not rec.is_me(victim) and rec.player(victim) is not None:
                return None     # someone else's death
        ctx = _death_context(rec, death_event)
        if ctx is None:
            return None
        return ctx["recap"]
    except Exception:
        log.exception("death_recap failed")
        return None


def is_my_death(record: Any, event: Any) -> bool:
    """True if ``event`` is a ``ChampionKill`` whose victim is me (riot id / summoner name / champion)."""
    try:
        return (isinstance(event, dict) and event.get("EventName") == "ChampionKill"
                and _Rec(record).is_me(event.get("VictimName")))
    except Exception:
        return False


# ======================================================================================
# sections
# ======================================================================================
def _summary(rec: _Rec, deaths: list[dict]) -> dict[str, Any]:
    snap = rec.last_snapshot()
    kills = deaths_n = assists = None
    if snap:
        kills, deaths_n, assists = _int(snap.get("kills")), _int(snap.get("deaths")), _int(snap.get("assists"))
    ev_kills = [e for e in rec.events if e.get("EventName") == "ChampionKill"]
    if kills is None:
        kills = sum(1 for e in ev_kills if rec.is_me(e.get("KillerName")))
        assists = sum(1 for e in ev_kills if any(rec.is_me(a) for a in _list(e.get("Assisters"))))
        deaths_n = len(deaths)
    deaths_n = max(deaths_n or 0, len(deaths)) if deaths and not snap else (deaths_n or 0)
    team_kills = None
    if rec.my_team and ev_kills:
        team_kills = 0
        for e in ev_kills:
            a = rec.actor(e.get("KillerName"))
            if a.get("kind") == "champion" and a.get("team") == rec.my_team:
                team_kills += 1
    duration = rec.duration
    minutes = duration / 60.0 if duration >= 60.0 else None
    cs = _int(snap.get("cs")) if snap else None
    ward = _finite(snap.get("ward_score")) if snap else None
    kp = None
    if team_kills:
        kp = min(1.0, ((kills or 0) + (assists or 0)) / team_kills)
    position = _str(rec.meta.get("position")).upper() or _str(rec.me.get("position")).upper()
    return {
        "champion": _str(rec.meta.get("champion")) or _str(rec.me.get("alias")),
        "champion_name": _str(rec.meta.get("champion_name")) or _str(rec.me.get("name"))
                         or _str(rec.meta.get("champion")),
        "team": rec.my_team,
        "position": position,
        "position_label": ROLE_FR.get(position, ""),
        "result": rec.result,
        "result_label": RESULT_FR.get(rec.result or "", "Partie non terminée"),
        "duration": round(duration, 1),
        "duration_text": fmt_time(duration),
        "kills": kills or 0,
        "deaths": deaths_n or 0,
        "assists": assists or 0,
        "kda_ratio": round(((kills or 0) + (assists or 0)) / max(1, deaths_n or 0), 2),
        "cs": cs,
        "cs_per_min": round(cs / minutes, 2) if (cs is not None and minutes) else None,
        "vision_score": ward,
        "vision_per_min": round(ward / minutes, 2) if (ward is not None and minutes) else None,
        "level": _int(snap.get("level"), 0) if snap else None,
        "gold": _int(snap.get("gold"), 0) if snap else None,
        "team_kills": team_kills,
        "kill_participation": round(kp, 3) if kp is not None else None,
        "game_mode": _str(rec.meta.get("game_mode")),
        "map_terrain": _str(rec.meta.get("map_terrain")) or "Default",
        "start": rec.meta.get("start"),
        "recorded_from": _finite(rec.meta.get("start_game_time"), 0.0),
        "complete": rec.result is not None and not rec.raw.get("incomplete", False),
        "app_version": _str(rec.meta.get("app_version")),
    }


def _deaths(rec: _Rec) -> list[dict]:
    events = _my_death_events(rec)
    if not events:
        events = _deaths_from_snapshots(rec)
    out = []
    for ev in events:
        ctx = _death_context(rec, ev)
        if ctx is not None:
            out.append(ctx)
    return out


def _enemies_near_me(rec: _Rec, t0: float, t1: float) -> list[str]:
    """Named enemies seen < NEAR_RADIUS from me between ``t0 - 3 s`` and ``t1 + 3 s``."""
    out: list[str] = []
    for key, ser in rec.sightings.items():
        if "?" in key:
            continue
        for gt, u, v in ser.window(t0 - 3.0, t1 + 3.0):
            mine = rec.my_pos.nearest(gt, MY_POS_MATCH_S)
            if mine is not None and geometry.dist((u, v), (mine[1], mine[2])) < NEAR_RADIUS:
                out.append(key)
                break
    return out


def _ganks(rec: _Rec, deaths: list[dict]) -> list[dict]:
    """DANGER threat alerts grouped in episodes; outcome = my death within 15 s."""
    danger = [a for a in rec.alerts if a[2] >= LEVEL_DANGER and a[1] not in NON_THREAT_KINDS]
    episodes: list[list[tuple]] = []
    for a in danger:
        if episodes and a[0] - episodes[-1][-1][0] <= GANK_MERGE_S:
            episodes[-1].append(a)
        else:
            episodes.append([a])
    death_times = [d["game_time"] for d in deaths]
    out = []
    for ep in episodes:
        t0, t_last = ep[0][0], ep[-1][0]
        died = [dt for dt in death_times if t0 - 0.5 <= dt <= t_last + GANK_OUTCOME_S]
        aliases: list[str] = []
        for a in ep:
            if a[4] and a[4] not in aliases:
                aliases.append(a[4])
        if not aliases:   # older records without alias: find champion names in the text
            for p in rec.roster:
                name = _str(p.get("name"))
                if name and name in " ".join(x[3] for x in ep) and _str(p.get("team")).upper() != rec.my_team:
                    aliases.append(_str(p.get("alias")) or name)
        if not aliases:   # e.g. COLLAPSE alerts: enemies seen near me when the alert was given
            aliases = _enemies_near_me(rec, t0, t_last)
        pre = [w for w in rec.alerts if t0 - 20.0 <= w[0] < t0 and w[2] == LEVEL_WARNING and w[1] in GANK_KINDS]
        out.append({
            "game_time": round(t0, 1),
            "time": fmt_time(t0),
            "end_time": round(t_last, 1),
            "kinds": sorted({a[1] for a in ep}),
            "text": ep[0][3],
            "alerts": len(ep),
            "aliases": aliases,
            "names": [rec.display(a) for a in aliases],
            "jungler": any(rec.is_jungler_key(a) for a in aliases) or any(a[1] == "jungler_approach" for a in ep),
            "outcome": "death" if died else "survived",
            "outcome_label": "mort" if died else "survécu",
            "death_time": round(died[0], 1) if died else None,
            "warning_lead_s": round(t0 - pre[0][0], 1) if pre else None,
        })
    return out


def _appearances(ser: _Series) -> list[list[tuple[float, float, float]]]:
    groups: list[list[tuple[float, float, float]]] = []
    for p in ser.pts:
        if groups and p[0] - groups[-1][-1][0] <= APPEARANCE_GAP_S:
            groups[-1].append(p)
        else:
            groups.append([p])
    return groups


def _kill_lane(rec: _Rec, T: float, victim: dict | None, jser: _Series | None) -> tuple[str, list[float] | None]:
    """Lane of a kill: zone of the jungler's (or my) position if it is a lane, else the victim's role."""
    pos = None
    if jser is not None:
        p = jser.nearest(T, KILL_POS_WINDOW_S)
        if p is not None:
            pos = (p[1], p[2])
    if pos is None and victim is not None and (victim is rec.me or victim.get("is_me") is True):
        p = rec.my_pos.last_before(T, DEATH_POS_WINDOW_S)
        if p is not None:
            pos = (p[1], p[2])
    if pos is not None:
        lane = geometry.lane_of(geometry.classify_zone(*pos))
        if lane:
            return lane, [round(pos[0], 3), round(pos[1], 3)]
    role_lane = LANE_ROLE.get(_str((victim or {}).get("position")).upper())
    if role_lane:
        return role_lane, [round(pos[0], 3), round(pos[1], 3)] if pos else None
    return "jungle", [round(pos[0], 3), round(pos[1], 3)] if pos else None


def _jungler(rec: _Rec) -> dict[str, Any]:
    j = rec.jungler
    out: dict[str, Any] = {
        "known": j is not None,
        "alias": _str(j.get("alias")) if j else None,
        "name": (_str(j.get("name")) or _str(j.get("alias"))) if j else None,
        "first_seen": None, "first_seen_time": None, "first_zone": None, "first_zone_label": None,
        "sightings": 0, "visible_s": 0.0, "appearances": 0,
        "by_phase": {name: {} for name, _, _ in PHASES},
        "zone_totals": {},
        "early_path": [],
        "ganks_by_lane": {"top": 0, "mid": 0, "bot": 0, "jungle": 0},
        "kills": [],
        "kills_involved": 0,
        "my_deaths": 0,
    }
    if j is None:
        return out
    ser = rec.jungler_series()
    if ser is not None and len(ser):
        first = ser.pts[0]
        z = _zone_value(first[1], first[2])
        out.update(first_seen=round(first[0], 1), first_seen_time=fmt_time(first[0]), first_zone=z,
                   first_zone_label=_zone_name(z, rec.my_team), sightings=len(ser))
        groups = _appearances(ser)
        out["appearances"] = len(groups)
        vis = 0.0
        for g in groups:
            vis += max(0.5, g[-1][0] - g[0][0] + 0.5)
            gt, u, v = g[0]
            zone = _zone_value(u, v)
            ph = out["by_phase"][phase_of(gt)]
            ph[zone] = ph.get(zone, 0) + 1
            out["zone_totals"][zone] = out["zone_totals"].get(zone, 0) + 1
        out["visible_s"] = round(vis, 1)
        for g in groups[:6]:
            gt, u, v = g[0]
            if gt > 600 and out["early_path"]:
                break
            zone = _zone_value(u, v)
            out["early_path"].append({"game_time": round(gt, 1), "time": fmt_time(gt), "zone": zone,
                                      "zone_label": _zone_name(zone, rec.my_team)})
    # kills he took part in (victim on my team)
    for e in rec.events:
        if e.get("EventName") != "ChampionKill":
            continue
        T = _finite(e.get("EventTime"))
        if T is None:
            continue
        names = [e.get("KillerName")] + _list(e.get("Assisters"))
        if not any(rec.player(n) is j for n in names if _str(n)):
            continue
        victim = rec.player(e.get("VictimName"))
        if victim is None or (rec.my_team and _str(victim.get("team")).upper() != rec.my_team):
            continue
        lane, pos = _kill_lane(rec, T, victim, ser)
        on_me = victim is rec.me or victim.get("is_me") is True
        out["kills"].append({"game_time": round(T, 1), "time": fmt_time(T), "lane": lane, "uv": pos,
                             "victim": _str(victim.get("name")) or _str(victim.get("alias")),
                             "killer": rec.player(e.get("KillerName")) is j, "on_me": on_me})
        out["ganks_by_lane"][lane] = out["ganks_by_lane"].get(lane, 0) + 1
        if on_me:
            out["my_deaths"] += 1
    out["kills_involved"] = len(out["kills"])
    return out


ZONE_GROUPS: tuple[tuple[str, str], ...] = (
    ("lane_top", "Voie du haut"), ("lane_mid", "Voie du milieu"), ("lane_bot", "Voie du bas"),
    ("river", "Rivière"), ("my_jungle", "Ta jungle"), ("enemy_jungle", "Jungle ennemie"),
    ("my_base", "Ta base"), ("enemy_base", "Base ennemie"),
)


def _zone_group(zone: str, my_team: str | None) -> str:
    z = Zone(zone)
    lane = geometry.lane_of(z)
    if lane:
        return f"lane_{lane}"
    if geometry.is_river(z):
        return "river"
    owner = geometry.zone_owner(z)
    mine = owner == (my_team or "ORDER")
    if geometry.is_base(z):
        return "my_base" if mine else "enemy_base"
    return "my_jungle" if mine else "enemy_jungle"


def _zones(rec: _Rec) -> dict[str, Any]:
    pts = rec.my_pos.pts
    seconds: dict[str, float] = {}
    for i, (gt, u, v) in enumerate(pts):
        dt = (pts[i + 1][0] - gt) if i + 1 < len(pts) else 1.0
        dt = min(MAX_SAMPLE_DT, max(0.0, dt))
        z = _zone_value(u, v)
        seconds[z] = seconds.get(z, 0.0) + dt
    total = sum(seconds.values())
    groups = {k: 0.0 for k, _ in ZONE_GROUPS}
    for z, s in seconds.items():
        groups[_zone_group(z, rec.my_team)] += s
    lane_s = groups["lane_top"] + groups["lane_mid"] + groups["lane_bot"]
    return {
        "total_s": round(total, 1),
        "seconds": {z: round(s, 1) for z, s in sorted(seconds.items(), key=lambda kv: -kv[1])},
        "percent": {z: round(100.0 * s / total, 1) for z, s in seconds.items()} if total else {},
        "groups": [{"key": k, "label": label, "seconds": round(groups[k], 1),
                    "percent": round(100.0 * groups[k] / total, 1) if total else 0.0}
                   for k, label in ZONE_GROUPS],
        "lane_percent": round(100.0 * lane_s / total, 1) if total else None,
    }


_OBJ_KEYS = ("dragons", "elder", "barons", "heralds", "grubs", "atakhan", "turrets", "inhibitors")
_OBJ_LABEL = {"dragons": "Dragon", "elder": "Dragon ancestral", "barons": "Baron Nashor", "heralds": "Héraut",
              "grubs": "Larve du Néant", "atakhan": "Atakhan", "turrets": "Tourelle", "inhibitors": "Inhibiteur"}
_DRAGON_FR = {"fire": "infernal", "water": "de l'océan", "earth": "des montagnes", "air": "des nuages",
              "hextech": "hextech", "chemtech": "chemtech", "elder": "ancestral"}


def _structure_owner(name: str) -> str | None:
    m = re.search(r"_T([12])_", name or "")
    if not m:
        return None
    return "ORDER" if m.group(1) == "1" else "CHAOS"


def _objectives(rec: _Rec) -> dict[str, Any]:
    teams = {t: {k: 0 for k in _OBJ_KEYS} for t in ("ORDER", "CHAOS")}
    timeline = []
    for e in rec.events:
        name = e.get("EventName")
        T = _finite(e.get("EventTime"), 0.0) or 0.0
        key = None
        team = None
        label = None
        if name == "DragonKill":
            elder = _str(e.get("DragonType")).lower() == "elder"
            key = "elder" if elder else "dragons"
            dtype = _DRAGON_FR.get(_str(e.get("DragonType")).lower())
            label = "Dragon ancestral" if elder else (f"Dragon {dtype}" if dtype else "Dragon")
        elif name == "BaronKill":
            key = "barons"
        elif name == "HeraldKill":
            key = "heralds"
        elif name == "HordeKill":
            key = "grubs"
        elif name == "AtakhanKill":
            key = "atakhan"
        elif name == "TurretKilled":
            key = "turrets"
            owner = _structure_owner(_str(e.get("TurretKilled")))
            team = {"ORDER": "CHAOS", "CHAOS": "ORDER"}.get(owner or "")
        elif name == "InhibKilled":
            key = "inhibitors"
            owner = _structure_owner(_str(e.get("InhibKilled")))
            team = {"ORDER": "CHAOS", "CHAOS": "ORDER"}.get(owner or "")
        if key is None:
            continue
        if team is None:
            for n in [e.get("KillerName")] + _list(e.get("Assisters")):
                p = rec.player(n)
                if p is not None and _str(p.get("team")).upper() in teams:
                    team = _str(p.get("team")).upper()
                    break
        if team in teams:
            teams[team][key] += 1
        timeline.append({"game_time": round(T, 1), "time": fmt_time(T), "kind": key,
                         "label": label or _OBJ_LABEL[key], "team": team,
                         "mine": team is not None and team == rec.my_team,
                         "stolen": _str(e.get("Stolen")).lower() == "true"})
    mine = teams.get(rec.my_team or "", None)
    theirs = teams.get(rec.enemy_team or "", None)
    return {"teams": teams, "mine": mine, "theirs": theirs, "timeline": timeline}


def _alert_stats(rec: _Rec) -> dict[str, Any]:
    by_kind: dict[str, int] = {}
    levels = [0, 0, 0]
    for a in rec.alerts:
        by_kind[a[1]] = by_kind.get(a[1], 0) + 1
        levels[a[2]] += 1
    return {"total": len(rec.alerts), "by_kind": by_kind, "info": levels[0], "warning": levels[1],
            "danger": levels[2]}


# ======================================================================================
# v2 coaching sections
# ======================================================================================
_EPIC_EVENTS = {"DragonKill": "dragon", "BaronKill": "baron", "HeraldKill": "herald", "HordeKill": "grubs",
                "AtakhanKill": "atakhan"}
_EPIC_LABEL = {"dragon": "Dragon", "baron": "Baron Nashor", "herald": "Héraut", "grubs": "Larves",
               "atakhan": "Atakhan"}
GROUP_KEYS = ("lane_top", "lane_mid", "lane_bot", "river", "my_jungle", "enemy_jungle", "my_base", "enemy_base")
_GROUP_LABEL = dict(ZONE_GROUPS)


def _pit_points(kind: str) -> list[tuple[float, float]]:
    d = (geometry.DRAGON_PIT[0], geometry.DRAGON_PIT[1])
    b = (geometry.BARON_PIT[0], geometry.BARON_PIT[1])
    return {"dragon": [d], "baron": [b], "herald": [b], "grubs": [b]}.get(kind, [d, b])


def _lane_frac(u: float, v: float, lane: str, my_team: str | None) -> float | None:
    """Fraction of ``lane`` from MY base (0) to the enemy base (1) at (u, v)."""
    try:
        from treeaicoach.waves import lane_position

        ln, s = lane_position(u, v)
    except Exception:
        return None
    if ln != lane or s is None:
        return None
    return 1.0 - s if my_team == "CHAOS" else s


def _jungler_last_seen(jser: _Series | None, gt: float) -> float | None:
    if jser is None or not len(jser):
        return None
    i = bisect.bisect_right(jser.times, gt) - 1
    return jser.pts[i][0] if i >= 0 else None


def _position_samples(rec: _Rec, t0: float = 0.0, t1: float = math.inf) -> list[tuple[float, float, float, float]]:
    """(gt, u, v, weight s) of my positions inside [t0, t1)."""
    pts = rec.my_pos.pts
    out = []
    for i, (gt, u, v) in enumerate(pts):
        if not t0 <= gt < t1:
            continue
        dt = (pts[i + 1][0] - gt) if i + 1 < len(pts) else 1.0
        out.append((gt, u, v, min(MAX_SAMPLE_DT, max(0.0, dt))))
    return out


def _exposure(rec: _Rec, t0: float = 0.0, t1: float = math.inf) -> dict[str, Any]:
    """Gank exposure: time past the middle of my lane / in the enemy jungle, enemy jungler unseen >= 30 s."""
    jser = rec.jungler_series()
    total = exposed = 0.0
    spots: list[list[float]] = []
    for gt, u, v, w in _position_samples(rec, t0, t1):
        z = geometry.classify_zone(u, v)
        if geometry.is_base(z):
            continue
        total += w
        last = _jungler_last_seen(jser, gt)
        unseen = (gt - last) if last is not None else (gt - 90.0)
        if rec.jungler is None or unseen < JUNGLER_UNSEEN_RISK_S or gt < 120.0:
            continue
        group = _zone_group(z.value, rec.my_team)
        risky = group == "enemy_jungle"
        lane = geometry.lane_of(z)
        if lane and not risky:
            f = _lane_frac(u, v, lane, rec.my_team)
            risky = f is not None and f >= EXPOSED_LANE_S
        if risky:
            exposed += w
            if len(spots) < 400:
                spots.append([round(u, 3), round(v, 3)])
    score = round(100.0 * exposed / total, 1) if total >= 30.0 else None
    return {"score": score, "exposed_s": round(exposed, 1), "total_s": round(total, 1), "spots": spots}


def _ideal_presence(role: str, phase: str) -> dict[str, float]:
    """Ideal share of time per zone group for a role and a game phase (sums to 1)."""
    lane = LANE_ROLE.get(role)
    g = {k: 0.0 for k in GROUP_KEYS}
    if role == "JUNGLE":
        table = {"laning": {"my_jungle": .45, "enemy_jungle": .10, "river": .18, "lane_top": .06, "lane_mid": .06,
                            "lane_bot": .06, "my_base": .09},
                 "mid": {"my_jungle": .30, "enemy_jungle": .12, "river": .25, "lane_top": .07, "lane_mid": .08,
                         "lane_bot": .10, "my_base": .08},
                 "late": {"my_jungle": .22, "enemy_jungle": .12, "river": .28, "lane_top": .08, "lane_mid": .12,
                          "lane_bot": .10, "my_base": .08}}
        g.update(table.get(phase, table["mid"]))
        return g
    if lane is None:
        lane = "mid"
    own = f"lane_{lane}"
    others = [k for k in ("lane_top", "lane_mid", "lane_bot") if k != own]
    if phase == "laning":
        river = .10 if role == "UTILITY" else .06
        g.update({own: .70 - (river - .06), "river": river, "my_jungle": .08, "enemy_jungle": .02, "my_base": .12})
        for k in others:
            g[k] = .01
    elif phase == "mid":
        g.update({own: .35, "river": .16, "my_jungle": .14, "enemy_jungle": .05, "my_base": .08})
        for k in others:
            g[k] = .11
    else:
        g.update({own: .22, "river": .22, "my_jungle": .12, "enemy_jungle": .06, "my_base": .06})
        for k in others:
            g[k] = .16
    total = sum(g.values())
    return {k: v / total for k, v in g.items()}


def _presence_in(rec: _Rec, t0: float, t1: float) -> tuple[dict[str, float], float]:
    secs = {k: 0.0 for k in GROUP_KEYS}
    for _gt, u, v, w in _position_samples(rec, t0, t1):
        secs[_zone_group(geometry.classify_zone(u, v).value, rec.my_team)] += w
    return secs, sum(secs.values())


def _presence(rec: _Rec, summary: dict) -> dict[str, Any]:
    role = _str(summary.get("position")).upper()
    out = {"role": role, "phases": []}
    for pid, label, t0, t1 in GAME_PHASES:
        if t0 >= max(rec.duration, 1.0):
            continue
        secs, total = _presence_in(rec, t0, t1)
        if total < 60.0:
            continue
        mine = {k: secs[k] / total for k in GROUP_KEYS}
        ideal = _ideal_presence(role, pid)
        l1 = sum(abs(mine[k] - ideal[k]) for k in GROUP_KEYS)
        rows = [{"key": k, "label": _GROUP_LABEL.get(k, k), "mine": round(100 * mine[k], 1),
                 "ideal": round(100 * ideal[k], 1), "delta": round(100 * (mine[k] - ideal[k]), 1)}
                for k in GROUP_KEYS if mine[k] > 0.005 or ideal[k] > 0.005]
        rows.sort(key=lambda r: -max(r["mine"], r["ideal"]))
        own = f"lane_{LANE_ROLE.get(role, 'mid')}" if role != "JUNGLE" else "my_jungle"
        out["phases"].append({"phase": pid, "label": label, "seconds": round(total, 1),
                              "match": round(max(0.0, 100.0 - 50.0 * l1), 0), "rows": rows,
                              "own_key": own, "own_mine": round(100 * mine.get(own, 0.0), 1),
                              "own_ideal": round(100 * ideal.get(own, 0.0), 1)})
    return out


def _objective_presence(rec: _Rec) -> dict[str, Any]:
    items = []
    for e in rec.events:
        kind = _EPIC_EVENTS.get(_str(e.get("EventName")))
        T = _finite(e.get("EventTime"))
        if kind is None or T is None:
            continue
        if kind == "dragon" and _str(e.get("DragonType")).lower() == "elder":
            kind = "elder"
        team = None
        for n in [e.get("KillerName")] + _list(e.get("Assisters")):
            p = rec.player(n)
            if p is not None and _str(p.get("team")).upper() in ("ORDER", "CHAOS"):
                team = _str(p.get("team")).upper()
                break
        took_part = any(rec.is_me(n) for n in [e.get("KillerName")] + _list(e.get("Assisters")) if _str(n))
        pos = rec.my_pos.nearest(T, OBJ_TIME_TOL_S)
        dist = None
        if pos is not None:
            dist = min(geometry.dist((pos[1], pos[2]), p) for p in _pit_points("dragon" if kind == "elder" else kind))
        near = took_part or (dist is not None and dist < OBJ_NEAR_R)
        items.append({"game_time": round(T, 1), "time": fmt_time(T), "kind": kind,
                      "label": "Dragon ancestral" if kind == "elder" else _EPIC_LABEL.get(kind, kind),
                      "team": team, "mine": team is not None and team == rec.my_team, "near": bool(near),
                      "took_part": took_part, "distance": round(dist, 3) if dist is not None else None})
    ours = [i for i in items if i["mine"]]
    theirs = [i for i in items if not i["mine"]]
    pct = round(100.0 * sum(1 for i in ours if i["near"]) / len(ours), 0) if ours else None
    return {"items": items, "ours": len(ours), "ours_near": sum(1 for i in ours if i["near"]),
            "theirs": len(theirs), "theirs_near": sum(1 for i in theirs if i["near"]), "percent": pct}


def _team_kills_until(rec: _Rec, gt: float) -> tuple[int, int, int]:
    """(team kills, my kills, my assists) of ChampionKill events up to ``gt``."""
    team = mine = assists = 0
    for e in rec.events:
        if e.get("EventName") != "ChampionKill":
            continue
        T = _finite(e.get("EventTime"), 0.0) or 0.0
        if T > gt:
            break
        a = rec.actor(e.get("KillerName"))
        if a.get("kind") == "champion" and rec.my_team and a.get("team") == rec.my_team:
            team += 1
        if rec.is_me(e.get("KillerName")):
            mine += 1
        if any(rec.is_me(x) for x in _list(e.get("Assisters"))):
            assists += 1
    return team, mine, assists


def _snapshot_at(rec: _Rec, gt: float) -> dict | None:
    times = [_finite(s.get("game_time"), 0.0) for s in rec.snapshots]
    i = bisect.bisect_right(times, gt) - 1
    return rec.snapshots[i] if i >= 0 else None


def _trends(rec: _Rec) -> dict[str, Any]:
    series = []
    if rec.snapshots:
        end = int(rec.duration // 60)
        for m in range(1, end + 1):
            snap = _snapshot_at(rec, m * 60.0)
            if snap is None:
                continue
            cs = _int(snap.get("cs"))
            team, k, a = _team_kills_until(rec, m * 60.0)
            series.append({"minute": m, "cs": cs, "cs_per_min": round(cs / m, 2),
                           "vision": round(_finite(snap.get("ward_score"), 0.0) or 0.0, 1),
                           "kp": round((k + a) / team, 3) if team else None, "level": _int(snap.get("level"), 0)})
    out: dict[str, Any] = {"series": series, "cs_per_min_10": None, "cs_per_min_after_10": None,
                           "vision_per_min_laning": None, "vision_per_min_after": None}
    s10 = _snapshot_at(rec, 600.0)
    last = rec.last_snapshot()
    if s10 is not None and rec.duration >= 600.0:
        cs10 = _int(s10.get("cs"))
        out["cs_per_min_10"] = round(cs10 / 10.0, 2)
        if rec.duration >= 900.0 and last:
            out["cs_per_min_after_10"] = round((_int(last.get("cs")) - cs10) / ((rec.duration - 600.0) / 60.0), 2)
    s14 = _snapshot_at(rec, 840.0)
    if s14 is not None and rec.duration >= 840.0:
        w14 = _finite(s14.get("ward_score"), 0.0) or 0.0
        out["vision_per_min_laning"] = round(w14 / 14.0, 2)
        if rec.duration >= 1140.0 and last:
            out["vision_per_min_after"] = round(((_finite(last.get("ward_score"), 0.0) or 0.0) - w14)
                                                / ((rec.duration - 840.0) / 60.0), 2)
    return out


def _phases(rec: _Rec, deaths: list[dict], ganks: list[dict]) -> list[dict[str, Any]]:
    out = []
    for pid, label, t0, t1 in GAME_PHASES:
        if t0 >= rec.duration:
            continue
        end = min(t1, rec.duration)
        minutes = max(1e-6, (end - t0) / 60.0)
        s0 = _snapshot_at(rec, t0) if t0 > 0 else (rec.snapshots[0] if rec.snapshots else None)
        s1 = _snapshot_at(rec, end)
        cs = vis = None
        if s0 is not None and s1 is not None:
            cs = max(0, _int(s1.get("cs")) - (_int(s0.get("cs")) if t0 > 0 else 0))
            vis = max(0.0, (_finite(s1.get("ward_score"), 0.0) or 0.0)
                      - ((_finite(s0.get("ward_score"), 0.0) or 0.0) if t0 > 0 else 0.0))
        kills = assists = 0
        for e in rec.events:
            T = _finite(e.get("EventTime"), -1.0)
            if e.get("EventName") != "ChampionKill" or not (t0 <= (T or 0) < end):
                continue
            if rec.is_me(e.get("KillerName")):
                kills += 1
            elif any(rec.is_me(a) for a in _list(e.get("Assisters"))):
                assists += 1
        secs, total = _presence_in(rec, t0, end)
        lane_s = secs["lane_top"] + secs["lane_mid"] + secs["lane_bot"]
        exp = _exposure(rec, t0, end)
        ph_deaths = [d for d in deaths if t0 <= d.get("game_time", -1) < end]
        ph_ganks = [g for g in ganks if t0 <= g.get("game_time", -1) < end]
        epic_ours = epic_theirs = 0
        for e in rec.events:
            T = _finite(e.get("EventTime"), -1.0) or -1.0
            if _EPIC_EVENTS.get(_str(e.get("EventName"))) and t0 <= T < end:
                p = rec.player(e.get("KillerName"))
                if p is not None and _str(p.get("team")).upper() == rec.my_team:
                    epic_ours += 1
                elif p is not None:
                    epic_theirs += 1
        out.append({
            "phase": pid, "label": label, "start": t0, "end": round(end, 1),
            "range": f"{fmt_time(t0)}–{fmt_time(end)}", "minutes": round(minutes, 1),
            "deaths": len(ph_deaths), "kills": kills, "assists": assists,
            "cs": cs, "cs_per_min": round(cs / minutes, 2) if cs is not None else None,
            "vision": round(vis, 1) if vis is not None else None,
            "vision_per_min": round(vis / minutes, 2) if vis is not None else None,
            "lane_percent": round(100.0 * lane_s / total, 1) if total >= 30 else None,
            "exposure": exp.get("score"),
            "ganks": len(ph_ganks), "ganks_survived": sum(1 for g in ph_ganks if g.get("outcome") == "survived"),
            "objectives_ours": epic_ours, "objectives_theirs": epic_theirs,
        })
    return out


def _pathing(rec: _Rec, jungler: dict) -> dict[str, Any]:
    out: dict[str, Any] = {"known": bool(jungler.get("known")), "name": jungler.get("name"),
                           "first_side": None, "first_side_label": None, "first_time": None,
                           "path": [], "gank_lanes": {}, "main_lane": None, "summary": ""}
    ser = rec.jungler_series()
    if not out["known"]:
        return out
    if ser is not None and len(ser):
        groups = _appearances(ser)
        for g in groups:
            gt, u, v = g[0]
            if gt > 900 or len(out["path"]) >= 10:
                break
            side = geometry.side_of(u, v)
            out["path"].append({"game_time": round(gt, 1), "time": fmt_time(gt), "uv": [round(u, 3), round(v, 3)],
                                "zone": _zone_value(u, v), "zone_label": _zone_name(_zone_value(u, v), rec.my_team),
                                "side": side})
        if out["path"]:
            first = out["path"][0]
            out["first_side"] = first["side"]
            out["first_side_label"] = "en haut" if first["side"] == "top" else "en bas"
            out["first_time"] = first["time"]
    lanes = {k: v for k, v in (jungler.get("ganks_by_lane") or {}).items() if k in ("top", "mid", "bot") and v}
    out["gank_lanes"] = lanes
    if lanes:
        out["main_lane"] = max(lanes.items(), key=lambda kv: kv[1])[0]
    parts = []
    name = jungler.get("name") or "Le jungler ennemi"
    if out["first_side"]:
        parts.append(f"{name} vu d'abord {out['first_side_label']} à {out['first_time']}")
    if out["main_lane"]:
        parts.append(f"ganks surtout {LANE_FR[out['main_lane']]} ({lanes[out['main_lane']]} kills)")
    out["summary"] = ", ".join(parts) + ("." if parts else "")
    return out


def spoken_summary(analysis: Any) -> str:
    """Short French end-of-game summary for the voice (<= ~40 words). Never raises."""
    try:
        a = analysis if isinstance(analysis, dict) else {}
        s = a.get("summary") or {}
        res = {"Win": "Victoire", "Lose": "Défaite"}.get(s.get("result") or "", "Partie terminée")
        mins = int(round((_finite(s.get("duration"), 0.0) or 0.0) / 60.0))
        head = f"{res} en {mins} minutes." if mins else f"{res}."
        k, d, ast = int(s.get("kills") or 0), int(s.get("deaths") or 0), int(s.get("assists") or 0)
        stats = (f"{_plural(k, 'kill')}, {_plural(d, 'mort')}, {_plural(ast, 'assistance')}")
        cspm = _finite(s.get("cs_per_min"))
        if cspm is not None:
            stats += f", {fmt_num(cspm, 0) if round(cspm, 1).is_integer() else fmt_num(cspm)} CS par minute"
        parts = [head, f"{stats}."]
        unseen = [d for d in a.get("deaths") or [] if d.get("jungler_unseen")]
        warned = a.get("deaths_warned") or 0
        if len(unseen) >= 2:
            parts.append(f"{len(unseen)} morts avec le jungler ennemi invisible.")
        elif warned >= 2:
            parts.append(f"{warned} morts juste après une alerte.")
        elif (a.get("ganks_faced") or 0) >= 2:
            parts.append(f"{a.get('ganks_survived', 0)} ganks survécus sur {a.get('ganks_faced')}.")
        sbd = a.get("scoreboard") if isinstance(a.get("scoreboard"), dict) else {}
        my_m = sbd.get("my_matchup") if isinstance(sbd.get("my_matchup"), dict) else None
        if my_m and my_m.get("enemy") and abs(_int(my_m.get("gold_diff"))) >= 500:
            gd = _int(my_m.get("gold_diff"))
            parts.append(f"Ta lane : {'avance' if gd > 0 else 'retard'} de "
                         f"{int(round(abs(gd), -2))} pièces d'or sur {my_m['enemy']}.")
        elif _int(sbd.get("praise_count")) >= 2:
            parts.append(f"{_int(sbd.get('praise_count'))} belles actions saluées.")
        tips = a.get("tip_items") or []
        first = next((t for t in tips if t.get("kind") == "warn"), tips[0] if tips else None)
        if first is not None:
            text = _str(first.get("speech") or first.get("text"))
            text = text.split(" : ")[-1] if " : " in text else text
            text = text[:1].upper() + text[1:]
            if not text.endswith((".", "!", "?")):
                text += "."
            parts.append(f"Priorité : {text[:1].lower() + text[1:]}")
        out = " ".join(parts)
        words = out.split()
        if len(words) > 45:
            out = " ".join(words[:45]).rstrip(",;:") + "."
        return out
    except Exception:
        log.exception("spoken_summary failed")
        return "Partie terminée. Le rapport est prêt."


# ======================================================================================
# Tab scoreboard (recorded scoreboard.ScoreboardSummary) + praise
# ======================================================================================
ROLE_FR_SHORT = {"TOP": "TOP", "JUNGLE": "JGL", "MIDDLE": "MID", "BOTTOM": "ADC", "UTILITY": "SUP"}


def _fmt_gold(n: Any) -> str:
    v = _int(n)
    a = abs(v)
    body = f"{a / 1000:.1f}".replace(".", ",") + " k" if a >= 1000 else str(a)
    return ("+" if v > 0 else "−" if v < 0 else "") + body + " PO"


def _scoreboard(rec: _Rec) -> dict[str, Any]:
    """Final Tab scoreboard (item gold, CS, levels per lane) + timeline + praise received."""
    sb = _dict(rec.raw.get("scoreboard"))
    final = _dict(sb.get("final"))
    timeline = []
    for row in _list(sb.get("timeline")):
        try:
            gt, diff = _finite(row[0]), _int(row[1])
            lanes = _dict(row[2]) if len(row) > 2 else {}
        except (TypeError, IndexError):
            continue
        if gt is not None:
            timeline.append({"game_time": round(gt, 1), "minute": int(gt // 60), "team_gold_diff": diff,
                             "lanes": {_str(k): [_int(x) for x in _list(v)[:3]] for k, v in lanes.items()}})
    matchups = []
    my = None
    for m in _list(final.get("matchups")):
        if not isinstance(m, dict):
            continue
        item = {"role": _str(m.get("role")), "role_short": ROLE_FR_SHORT.get(_str(m.get("role")), _str(m.get("role"))),
                "ally": _str(m.get("ally")), "enemy": _str(m.get("enemy")),
                "ally_alias": _str(m.get("ally_alias")), "enemy_alias": _str(m.get("enemy_alias")),
                "gold_diff": _int(m.get("gold_diff")), "cs_diff": _int(m.get("cs_diff")),
                "level_diff": _int(m.get("level_diff")), "kills_diff": _int(m.get("kills_diff")),
                "involves_me": bool(m.get("involves_me"))}
        item["gold_label"] = _fmt_gold(item["gold_diff"])
        matchups.append(item)
        if item["involves_me"]:
            my = item
    by_alias = {_str(p.get("alias")): p for p in _list(final.get("players")) if isinstance(p, dict)}

    def names(aliases: Any) -> list[str]:
        return [_str(by_alias.get(_str(a), {}).get("name")) or _str(a) for a in _list(aliases)]
    praise = [{"game_time": round(a[0], 1), "time": fmt_time(a[0]), "text": a[3], "alias": a[4]}
              for a in rec.alerts if a[1] == "praise"]
    # lead history of my lane (from the timeline)
    my_role = my["role"] if my else None
    lane_curve = [(r["minute"], r["lanes"][my_role][0]) for r in timeline
                  if my_role and my_role in r["lanes"] and r["lanes"][my_role]]
    best_lead = max((g for _m, g in lane_curve), default=None)
    worst_lead = min((g for _m, g in lane_curve), default=None)
    return {
        "available": bool(final.get("players")),
        "team_gold_diff": _int(final.get("team_gold_diff")),
        "team_gold_label": _fmt_gold(final.get("team_gold_diff")),
        "ally_kills": _int(final.get("ally_kills")), "enemy_kills": _int(final.get("enemy_kills")),
        "matchups": matchups, "my_matchup": my,
        "fed": names(final.get("fed")), "struggling": names(final.get("struggling")),
        "spikes": [_str(x) for x in _list(final.get("spikes"))],
        "timeline": timeline, "lane_curve": lane_curve,
        "best_lead": best_lead, "worst_lead": worst_lead,
        "praise": praise, "praise_count": len(praise),
    }


# ======================================================================================
# tips
# ======================================================================================
CS_TARGET = {"TOP": 7.0, "MIDDLE": 7.0, "BOTTOM": 7.5, "JUNGLE": 5.5, "": 7.0}
VISION_TARGET = {"UTILITY": 2.0, "JUNGLE": 1.0}      # others: VISION_TARGET_DEFAULT
VISION_TARGET_DEFAULT = 0.6


def _first_uv(rec: _Rec) -> tuple[float, float]:
    ser = rec.jungler_series()
    if ser is not None and len(ser):
        return ser.pts[0][1], ser.pts[0][2]
    return 0.5, 0.5


def _ward_time(first_gank: float) -> str:
    """Suggested river-ward time: 45 s before the first gank, rounded down to 15 s, >= 2:45."""
    t = max(165.0, first_gank - 45.0)
    return fmt_time(int(t // 15) * 15)


def _tips(rec: _Rec, summary: dict, deaths: list[dict], ganks: list[dict], jungler: dict,
          zones: dict, objectives: dict, extra: dict | None = None) -> list[dict[str, Any]]:
    """Rule-based tips, most important first. Each rule is explained in the comment above it."""
    tips: list[tuple[int, str, str, str]] = []     # (priority, rule id, kind, text)
    extra = extra or {}
    minutes = (summary.get("duration") or 0.0) / 60.0
    position = summary.get("position") or ""
    n_deaths = len(deaths)
    jname = jungler.get("name") or "Le jungler ennemi"

    # R1 — deaths after a gank alert: the voice warned within 12 s and I still died.
    ignored = [d for d in deaths if d.get("warned")]
    if ignored:
        avg = sum(d.get("alert_before_s") or 0 for d in ignored) / len(ignored)
        tips.append((100, "ignored_alerts", "warn",
                     f"{_plural(len(ignored), 'mort')} dans les 12 s après une alerte "
                     f"(en moyenne {fmt_num(avg, 0)} s après l'annonce) : recule dès l'annonce vocale."))

    # R2 — the enemy jungler took part in >= 2 of my deaths (kill event or seen near me).
    jd = [d for d in deaths if d.get("jungler_involved")]
    if jungler.get("known") and len(jd) >= 2:
        tips.append((90, "jungler_deaths", "warn",
                     f"{jname} a participé à {len(jd)} de tes {n_deaths} morts : quand il est invisible "
                     f"depuis plus de 30 s, joue près de ta tour."))

    # R3 — >= 2 deaths with no alert and no enemy visible near me: I was caught in the fog.
    blind = [d for d in deaths if not d.get("warned") and not d.get("nearby")]
    if len(blind) >= 2:
        tips.append((85, "blind_deaths", "warn",
                     f"{len(blind)} morts sans alerte ni ennemi visible près de toi : avance seulement "
                     f"quand ta vision couvre la rivière et les entrées de jungle."))

    # R4 — the enemy jungler killed >= 2 times in one lane (and it is his most ganked lane):
    #      ward the matching river before his first gank there.
    lanes = {k: v for k, v in (jungler.get("ganks_by_lane") or {}).items() if k in ("top", "mid", "bot")}
    if lanes:
        lane, cnt = max(lanes.items(), key=lambda kv: kv[1])
        if cnt >= 2:
            first = min((k["game_time"] for k in jungler.get("kills", []) if k.get("lane") == lane), default=240.0)
            where = {"top": "la rivière du haut", "bot": "la rivière du bas",
                     "mid": "les entrées de la voie du milieu"}[lane]
            tips.append((80, "jungler_lane", "warn",
                         f"{jname} a ganké {cnt} fois {LANE_FR[lane]} : balise {where} vers {_ward_time(first)}."))

    # R5 — >= 2 deaths before 10:00: early game too risky.
    early = [d for d in deaths if d.get("game_time", 1e9) < 600]
    if len(early) >= 2:
        tips.append((75, "early_deaths", "warn",
                     f"{len(early)} morts avant 10:00 : en début de partie, joue prudemment tant que "
                     f"le jungler ennemi n'a pas été vu."))

    # R6 — CS/min below the role target (7+ for solo lanes, 7,5 for ADC, 5,5 jungle; no tip for
    #      supports) by more than 0.3, games >= 10 min: missed CS in minions over the game.
    cspm = summary.get("cs_per_min")
    target = CS_TARGET.get(position) if position != "UTILITY" else None
    if cspm is not None and target is not None and minutes >= 10 and cspm < target - 0.3:
        missed = int(round((target - cspm) * minutes))
        tips.append((70, "cs", "warn",
                     f"CS/min {fmt_num(cspm)} : objectif {fmt_target(target)}+ "
                     f"(≈ {missed} sbires de plus sur {int(minutes)} min)."))
    elif cspm is not None and target is not None and minutes >= 10 and cspm >= target:
        tips.append((30, "cs_good", "good", f"CS/min {fmt_num(cspm)} : très bon farm, garde ce rythme."))

    # R7 — vision score per minute below the role target (2,0 support, 1,0 jungle, 0,6 others).
    vpm = summary.get("vision_per_min")
    vt = VISION_TARGET.get(position, VISION_TARGET_DEFAULT)
    if vpm is not None and minutes >= 10 and vpm < vt * 0.8:
        tips.append((65, "vision", "warn",
                     f"Score de vision {fmt_num(vpm)}/min : vise {fmt_target(vt)}+ — pose tes balises et achète "
                     f"une balise de contrôle à chaque retour."))

    # R8 — >= 2 deaths in the river or the enemy jungle: over-extension without vision.
    risky = [d for d in deaths if d.get("zone") and _zone_group(d["zone"], rec.my_team) in ("river", "enemy_jungle")]
    if len(risky) >= 2:
        tips.append((60, "risky_zone_deaths", "warn",
                     f"{len(risky)} morts dans la rivière ou la jungle ennemie : n'y entre qu'avec de la vision "
                     f"ou ton équipe."))

    # R9 — enemy took >= 2 more dragons than us: prepare the bot-side river before spawns.
    mine, theirs = objectives.get("mine"), objectives.get("theirs")
    if mine and theirs and theirs["dragons"] - mine["dragons"] >= 2:
        tips.append((55, "dragons", "warn",
                     f"Dragons : {mine['dragons']} contre {theirs['dragons']} : place-toi côté bas une minute "
                     f"avant l'apparition (annonce vocale)."))

    # R10 — kill participation < 35 % (>= 10 team kills) for roaming roles: join more fights.
    kp = summary.get("kill_participation")
    if kp is not None and (summary.get("team_kills") or 0) >= 10 and kp < 0.35 and position in ("JUNGLE", "UTILITY", "MIDDLE"):
        tips.append((50, "kill_participation", "warn",
                     f"Participation aux kills {fmt_num(kp * 100, 0)} % : rejoins davantage les combats "
                     f"de ton équipe."))

    # R11 — positive: survived >= 70 % of >= 2 ganks.
    if len(ganks) >= 2:
        surv = sum(1 for g in ganks if g.get("outcome") == "survived")
        if surv / len(ganks) >= 0.7:
            tips.append((45, "ganks_survived", "good",
                         f"Tu as survécu à {surv} ganks sur {len(ganks)} : bonne réaction aux annonces, "
                         f"continue ainsi."))

    # R12 — positive: <= 2 deaths in a game of >= 15 min.
    if minutes >= 15 and n_deaths <= 2:
        tips.append((40, "few_deaths", "good",
                     f"Seulement {_plural(n_deaths, 'mort')} en {int(minutes)} min : excellente discipline."))

    # R13 — first jungler sighting before 5:00: tells his starting side for the next games.
    if jungler.get("first_seen") is not None and jungler["first_seen"] < 300:
        side = "en haut" if geometry.side_of(*_first_uv(rec)) == "top" else "en bas"
        tips.append((50, "jungler_start", "info",
                     f"{jname} a été vu pour la première fois à {jungler['first_seen_time']} "
                     f"({jungler['first_zone_label']}, côté {side.split()[-1]}) : en début de partie, "
                     f"attends-toi à son premier gank {side} vers {fmt_time(max(165.0, jungler['first_seen'] + 20))}."))

    # R14 — >= 2 deaths while the enemy jungler had been unseen for 30 s or more.
    unseen = [d for d in deaths if d.get("jungler_unseen")]
    if jungler.get("known") and len(unseen) >= 2:
        avg = sum(d.get("jungler_unseen_s") or 0 for d in unseen) / len(unseen)
        tips.append((72, "jungler_unseen_deaths", "warn",
                     f"{len(unseen)} morts alors que {jname} était invisible depuis {fmt_num(avg, 0)} s en "
                     f"moyenne : quand il disparaît, recule vers ta tour ou balise son chemin."))

    # R15 — gank exposure during the laning phase >= 25 %: pushed past the middle blind.
    exp_lane = next((p.get("exposure") for p in extra.get("phases") or [] if p.get("phase") == "laning"), None)
    if exp_lane is not None and exp_lane >= 25 and jungler.get("known"):
        tips.append((62, "exposure", "warn",
                     f"Exposition aux ganks {fmt_num(exp_lane, 0)} % en phase de voie (avancé sans savoir où "
                     f"était {jname}) : vise moins de 15 %, pousse quand il est vu ailleurs."))

    # R16 — present on < 50 % of the (>= 3) epic objectives taken by my team.
    op = extra.get("objective_presence") or {}
    if (op.get("ours") or 0) >= 3 and op.get("percent") is not None and op["percent"] < 50:
        tips.append((57, "objective_presence", "warn",
                     f"Présent sur {op.get('ours_near')} des {op.get('ours')} objectifs pris par ton équipe : "
                     f"rejoins le dragon ou le Baron 45 s avant l'apparition."))
    elif (op.get("ours") or 0) >= 3 and (op.get("percent") or 0) >= 75:
        tips.append((32, "objective_presence_good", "good",
                     f"Présent sur {op.get('ours_near')} des {op.get('ours')} objectifs de ton équipe : "
                     f"excellent réflexe de regroupement."))

    # R17 — laning phase: time in my own lane far below the ideal for my role.
    pres = next((p for p in (extra.get("presence") or {}).get("phases") or [] if p.get("phase") == "laning"), None)
    if pres is not None and position not in ("JUNGLE", "") and pres["own_mine"] < pres["own_ideal"] - 20:
        tips.append((56, "presence", "warn",
                     f"Phase de voie : {fmt_num(pres['own_mine'], 0)} % de ton temps dans ta voie (idéal "
                     f"≈ {fmt_num(pres['own_ideal'], 0)} %) : chaque minute hors de la voie coûte de l'or et de l'XP."))

    # R18 — >= 3 deaths in the mid or late game.
    for ph in extra.get("phases") or []:
        if ph.get("phase") in ("mid", "late") and (ph.get("deaths") or 0) >= 3:
            tips.append((66, f"phase_deaths_{ph['phase']}", "warn",
                         f"{ph['deaths']} morts en {ph['label'].lower()} ({ph['range']}) : reste groupé et "
                         f"ne pars pas seul en side sans vision."))
            break

    # R19 — CS/min dropping after 10:00 by >= 1.
    tr = extra.get("trends") or {}
    a10, b10 = tr.get("cs_per_min_10"), tr.get("cs_per_min_after_10")
    if a10 is not None and b10 is not None and position != "UTILITY" and a10 - b10 >= 1.0:
        tips.append((33, "cs_trend", "warn",
                     f"CS/min {fmt_num(a10)} avant 10:00 puis {fmt_num(b10)} : en milieu de partie, "
                     f"continue de farmer les vagues de côté entre deux objectifs."))

    # Fallbacks (always true, with numbers) so the report has at least 3 tips.
    fallbacks: list[tuple[int, str, str, str]] = []
    lp = zones.get("lane_percent")
    if lp is not None and zones.get("total_s", 0) >= 120:
        fallbacks.append((20, "lane_time", "info",
                          f"{fmt_num(lp, 0)} % de ton temps visible passé en voie : plus tu es en voie, plus tu "
                          f"gagnes d'or et d'expérience."))
    if ganks:
        surv = sum(1 for g in ganks if g.get("outcome") == "survived")
        fallbacks.append((15, "ganks_count", "info",
                          f"{_plural(len(ganks), 'gank')} détecté{'s' if len(ganks) > 1 else ''}, "
                          f"{surv} survécu{'s' if surv > 1 else ''} : garde la voix active pour chaque partie."))
    fallbacks.append((10, "kda", "info",
                      f"K/D/A {summary.get('kills', 0)}/{summary.get('deaths', 0)}/{summary.get('assists', 0)} "
                      f"(ratio {fmt_num(summary.get('kda_ratio'), 1)}) : chaque mort évitée compte plus qu'un kill."))
    fallbacks.append((7, "river_ward", "info",
                      "Pose une balise dans la rivière vers 2:45 : les premiers ganks arrivent souvent "
                      "entre 3:00 et 4:00."))
    fallbacks.append((5, "minimap", "info",
                      "Regarde la minimap toutes les 5 secondes : les annonces vocales complètent, "
                      "mais ne remplacent pas, ta vigilance."))
    sbd = extra.get("scoreboard") or {}
    my_m = sbd.get("my_matchup") if isinstance(sbd, dict) else None
    if isinstance(my_m, dict) and my_m.get("enemy"):
        gd, csd = _int(my_m.get("gold_diff")), _int(my_m.get("cs_diff"))
        if gd <= -1500 or csd <= -30:
            tips.append((62, "lane_lost", "warn",
                         f"Lane perdue contre {my_m['enemy']} ({_fmt_gold(gd)}, {csd:+d} CS) : joue plus "
                         f"sous ta tour quand il a l'avantage et rattrape-toi au farm."))
        elif gd >= 1500 and csd >= 0:
            tips.append((34, "lane_won", "good",
                         f"Lane gagnée contre {my_m['enemy']} ({_fmt_gold(gd)}, {csd:+d} CS) : "
                         f"transforme cette avance en tours et en objectifs."))
    tips.sort(key=lambda x: -x[0])
    chosen = tips[:MAX_TIPS]
    # keep one encouraging tip when there is one (the list is full of warnings otherwise)
    goods = [t for t in tips if t[2] == "good"]
    if goods and not any(t[2] == "good" for t in chosen) and len(chosen) == MAX_TIPS:
        chosen[-1] = goods[0]
    ids = {t[1] for t in chosen}
    for fb in fallbacks:
        if len(chosen) >= MIN_TIPS:
            break
        if fb[1] not in ids:
            chosen.append(fb)
    return [{"text": text, "rule": rule, "kind": kind, "priority": prio} for prio, rule, kind, text in chosen]


# ======================================================================================
# public entry point
# ======================================================================================
def analyze_game(record: Any) -> dict[str, Any]:
    """Analyse a game record (see module docstring). Pure, never raises."""
    errors: list[str] = []
    out: dict[str, Any] = {"schema": 1, "ok": False, "errors": errors}
    try:
        rec = _Rec(record)
        rec.finalize_duration()
    except Exception as exc:
        log.exception("analyze_game: unreadable record")
        errors.append(f"record: {exc}")
        rec = _Rec({})

    def section(name: str, fn, default):
        try:
            return fn()
        except Exception as exc:  # one broken section never hides the others
            log.exception("analyze_game: section %s failed", name)
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            return default

    deaths = section("deaths", lambda: _deaths(rec), [])
    summary = section("summary", lambda: _summary(rec, deaths), {})
    ganks = section("ganks", lambda: _ganks(rec, deaths), [])
    jungler = section("jungler", lambda: _jungler(rec), {"known": False})
    zones = section("zones", lambda: _zones(rec), {})
    objectives = section("objectives", lambda: _objectives(rec), {})
    alerts = section("alerts", lambda: _alert_stats(rec), {})
    phases = section("phases", lambda: _phases(rec, deaths, ganks), [])
    presence = section("presence", lambda: _presence(rec, summary), {"phases": []})
    exposure = section("exposure", lambda: _exposure(rec), {"score": None})
    obj_presence = section("objective_presence", lambda: _objective_presence(rec), {"items": []})
    trends = section("trends", lambda: _trends(rec), {"series": []})
    pathing = section("pathing", lambda: _pathing(rec, jungler), {"known": False})
    scoreboard = section("scoreboard", lambda: _scoreboard(rec), {"available": False, "praise": []})
    extra = {"phases": phases, "presence": presence, "exposure": exposure, "objective_presence": obj_presence,
             "trends": trends, "pathing": pathing, "scoreboard": scoreboard}
    tip_items = section("tips", lambda: _tips(rec, summary, deaths, ganks, jungler, zones, objectives, extra), [])
    survived = sum(1 for g in ganks if g.get("outcome") == "survived")
    out.update({
        "ok": not errors,
        "summary": summary,
        "deaths": deaths,
        "deaths_warned": sum(1 for d in deaths if d.get("warned")),
        "deaths_unwarned": sum(1 for d in deaths if not d.get("warned")),
        "ganks": ganks,
        "ganks_faced": len(ganks),
        "ganks_survived": survived,
        "jungler": jungler,
        "zones": zones,
        "objectives": objectives,
        "alerts": alerts,
        "tips": [t["text"] for t in tip_items],
        "tip_items": tip_items,
        **extra,
    })
    out["spoken_summary"] = spoken_summary(out)
    return out


def iter_phase_names() -> Iterable[tuple[str, str]]:
    """(phase id, French label) in order."""
    return [(name, PHASE_LABELS_FR[name]) for name, _, _ in PHASES]
