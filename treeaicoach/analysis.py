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
                                              "jungler_where", "death_recap"})
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
# tips
# ======================================================================================
CS_TARGET = {"TOP": 7.0, "MIDDLE": 7.0, "BOTTOM": 7.5, "JUNGLE": 5.5, "": 7.0}
VISION_TARGET = {"UTILITY": 2.0, "JUNGLE": 1.0}      # others: VISION_TARGET_DEFAULT
VISION_TARGET_DEFAULT = 0.6


def _ward_time(first_gank: float) -> str:
    """Suggested river-ward time: 45 s before the first gank, rounded down to 15 s, >= 2:45."""
    t = max(165.0, first_gank - 45.0)
    return fmt_time(int(t // 15) * 15)


def _tips(rec: _Rec, summary: dict, deaths: list[dict], ganks: list[dict], jungler: dict,
          zones: dict, objectives: dict) -> list[dict[str, Any]]:
    """Rule-based tips, most important first. Each rule is explained in the comment above it."""
    tips: list[tuple[int, str, str, str]] = []     # (priority, rule id, kind, text)
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
        tips.append((35, "jungler_start", "info",
                     f"{jname} a été vu pour la première fois à {jungler['first_seen_time']} "
                     f"({jungler['first_zone_label']}) : surveille ce côté en début de partie."))

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
    fallbacks.append((5, "minimap", "info",
                      "Regarde la minimap toutes les 5 secondes : les annonces vocales complètent, "
                      "mais ne remplacent pas, ta vigilance."))
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
    tip_items = section("tips", lambda: _tips(rec, summary, deaths, ganks, jungler, zones, objectives), [])
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
    })
    return out


def iter_phase_names() -> Iterable[tuple[str, str]]:
    """(phase id, French label) in order."""
    return [(name, PHASE_LABELS_FR[name]) for name, _, _ in PHASES]
