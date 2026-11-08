"""WoW Forever « en direct » : the game's own files watched cheaply, parsed into snapshots for the WoW page.

Blizzard rules: only files the game writes itself are read, never memory / screen / keys.

* ``WTF/Account/*/SavedVariables/TreeAI.lua`` (``TreeAIDB``) — written by WoW at /reload, logout, quit:
  summaries of every addon module (quests, tracker, predict, missions, auction, boost, fight, economy…).
  NOT live: the page always says « données de ton dernier /reload il y a N min ».
* ``WTF/Account/*/SavedVariables/Auctionator.lua`` (``AUCTIONATOR_PRICE_DATABASE``) — the user's own
  Auctionator price history, read-only, parsed only when its mtime changes (it can be big).
* ``Logs/WoWChatLog.txt`` (``/chatlog`` in game) — really live: XP, money, loot, level, zone, deaths.
* ``Logs/WoWCombatLog.txt`` (``/combatlog``) — live fights when the client writes it (Forever may not).

Watchers poll ``mtime``/size (one ``stat`` per file per tick); :class:`LiveHub.poll` returns change
events and keeps a snapshot dict. The ``*_view`` functions turn a snapshot into what each tab of the
page shows (pure, no Qt, tested with fake files).

(4.0) Cheap and honest: TreeAI.lua is parsed once per save for every reader (:func:`read_db_cached`: the WoW
mode, the Accueil card and the page share it); Auctionator's database is never fully parsed (:class:`AtrIndex`
reads only the realm and the items the page shows); every number says where it comes from and how old it is
(:func:`freshness`, :func:`xp_rate`), and nothing measured over less than 10 min is shown."""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

AUCTIONATOR_VAR = "AUCTIONATOR_PRICE_DATABASE"
#: Auctionator's « scan day 0 » is 2020-01-01; its history keys are days since then.
AUCTIONATOR_DAY0 = 18262                  # days from 1970-01-01 to 2020-01-01
SESSION_GAP_S = 30 * 60                   # chat silence that starts a new live session
LIVE_WINDOW_S = 10 * 60                   # a log written in the last 10 min counts as « en direct »
BUCKET_S = 300                            # sparkline buckets of the live session (5 min)
CHAT_MAX_BYTES = 1 << 20
STALE_S = 30 * 60                         # older data is never presented as current
OLD_S = 6 * 3600                          # « dernière session » rather than « maintenant »
XP_MIN_S = 10 * 60                        # an XP/h measured over less than 10 min is not shown
AH_MAX_AGE_S = 24 * 3600                  # no deal from a scan older than 24 h
VOLATILE_S = 5 * 60                       # « Alpha est mort », « sacs pleins »: shown only from a fresh save
ATR_MAX_BYTES = 160 << 20                 # Auctionator files above this are ignored
BLOCK_STALE_S = 10 * 60                   # a summary block this much older than the summary: its module stopped
DEATH_RECENT_S = 2 * 3600                 # « Dernière mort » only for a death of the last 2 h
MOIS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")


# ------------------------------------------------------------------------------------- helpers
def _i(v: Any, default: int = 0) -> int:
    try:
        if isinstance(v, bool):
            return default
        x = float(v)
        return int(x) if math.isfinite(x) else default
    except Exception:
        return default


def _f(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _list(v: Any) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return [v[k] for k in sorted(k for k in v if isinstance(k, int))]
    return []


def _d(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def money(copper: Any) -> str:
    """Copper → « 1 po 20 pa » (short, sign kept, copper dropped above 10 po)."""
    c = _i(copper)
    sign, c = ("-" if c < 0 else ""), abs(c)
    g, s, cp = c // 10000, (c // 100) % 100, c % 100
    parts = [f"{g} po"] if g else []
    if s:
        parts.append(f"{s} pa")
    if (cp and g < 10) or not parts:
        parts.append(f"{cp} pc")
    return sign + " ".join(parts)


def age_text(age: Any) -> str:
    if not isinstance(age, (int, float)) or isinstance(age, bool):
        return ""
    if age < 90:
        return "à l'instant"
    if age < 3600:
        return f"il y a {int(age // 60)} min"
    if age < 86400:
        return f"il y a {int(age // 3600)} h"
    return f"il y a {int(age // 86400)} j"


def freshness_text(age: Any) -> str:
    """The honest banner: SavedVariables are only written at /reload or logout."""
    if not isinstance(age, (int, float)) or isinstance(age, bool):
        return "Pas encore de sauvegarde de l'addon : connecte-toi puis tape /reload en jeu."
    if age < 90:
        return "Données de ton dernier /reload, à l'instant."
    return f"Données de ton dernier /reload {age_text(age)}. Tape /reload pour les rafraîchir."


def duration_text(sec: Any) -> str:
    s = _i(sec)
    if s <= 0:
        return ""
    if s < 3600:
        return f"{max(1, s // 60)} min"
    return f"{s // 3600} h {s % 3600 // 60:02d}"


# ------------------------------------------------------------------------------------- watchers
class FileWatch:
    """mtime + size polling of one file: ``check()`` is True when it appeared or changed. One stat."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self.sig: tuple[int, int] | None = None
        self.mtime: float | None = None

    def check(self) -> bool:
        if self.path is None:
            return False
        try:
            st = self.path.stat()
        except OSError:
            changed = self.sig is not None
            self.sig, self.mtime = None, None
            return changed
        sig = (int(st.st_mtime_ns), int(st.st_size))
        if sig == self.sig:
            return False
        self.sig, self.mtime = sig, float(st.st_mtime)
        return True

    def age(self, now: float | None = None) -> float | None:
        if self.mtime is None:
            return None
        return max(0.0, (time.time() if now is None else now) - self.mtime)


@dataclass
class LiveEvent:
    """A change seen by :meth:`LiveHub.poll`: ``kind`` in savedvars / auctionator / chat / combat."""
    kind: str
    detail: str = ""
    data: Any = None


# ------------------------------------------------------------------------------------- chat log
_TS_RE = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s+(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?\s+(.*)$")
_SPEECH_RE = re.compile(r"^\[|\b(dit|says|chuchote|whispers|crie|yells)\b", re.I)
_XP_RE = re.compile(r"(\d[\d  .]*)\s*points? d['’]expérience|gain (\d[\d,]*) experience", re.I)
_LEVEL_RE = re.compile(r"(?:atteint le \[?niveau|reached \[?level)\s+(\d+)|\|Hlevelup:(\d+)", re.I)
_DEATH_RE = re.compile(r"^(vous êtes mort|vous mourez|you die|you have died)", re.I)
_MONEY_WORD_RE = re.compile(r"(\d+)\s*(pièces? d['’]or|or|argent|cuivre|gold|silver|copper)\b", re.I)
_MONEY_CTX_RE = re.compile(r"ramass|part du butin|\bloot|share|recev|receiv|gagn", re.I)
_LOOT_RE = re.compile(r"(?:Vous recevez le butin|Vous recevez l['’]objet|You receive loot|You receive item|"
                      r"Votre butin)\s*:\s*\[([^\]]+)\](?:\s*x(\d+))?", re.I)
_ZONE_RE = re.compile(r"(?:Changed Channel|Joined Channel|Canal changé|Canal modifié|Canal rejoint)\s*:\s*"
                      r"\[\d+\.\s*[^\]]*?-\s*([^\]]+)\]", re.I)
_MONEY_UNIT = {"or": 10000, "gold": 10000, "argent": 100, "silver": 100, "cuivre": 1, "copper": 1}


def _num(s: str) -> int:
    return _i(re.sub(r"[^\d]", "", s or ""))


def parse_money(text: str) -> int:
    """« 1 Or, 20 Argent, 5 Cuivre » / « 2 Silver, 5 Copper » → copper (0 when none)."""
    total = 0
    for n, unit in _MONEY_WORD_RE.findall(text or ""):
        u = unit.lower()
        total += int(n) * (10000 if u.startswith("pièce") else _MONEY_UNIT.get(u, 0))
    return total


def parse_chat_line(line: str, year: int | None = None) -> dict | None:
    """One WoWChatLog.txt line → {t, kind, …} (xp, level, death, money, loot, zone) or None.

    Format: ``11/7 20:15:03.456  Vous gagnez 120 points d'expérience.`` (French and English clients)."""
    m = _TS_RE.match((line or "").strip())
    if not m:
        return None
    mo, dd, yy, hh, mi, ss, frac, text = m.groups()
    y = _i(yy) if yy else (year or time.localtime().tm_year)
    if y < 100:
        y += 2000
    try:
        t = time.mktime((y, int(mo), int(dd), int(hh), int(mi), int(ss), 0, 0, -1)) + (
            float("0." + frac) if frac else 0.0)
    except (OverflowError, ValueError):
        return None
    text = text.strip()
    z = _ZONE_RE.search(text)
    if z:
        return {"t": t, "kind": "zone", "zone": z.group(1).strip()}
    if _SPEECH_RE.search(text):
        return None
    if _DEATH_RE.search(text):
        return {"t": t, "kind": "death"}
    lv = _LEVEL_RE.search(text)
    if lv:
        return {"t": t, "kind": "level", "level": int(lv.group(1) or lv.group(2))}
    xp = _XP_RE.search(text)
    if xp:
        val = _num(xp.group(1) or xp.group(2) or "")
        if val > 0:
            return {"t": t, "kind": "xp", "xp": val, "kill": bool(re.search(r"meurt|dies|tué|slain", text, re.I))}
    lt = _LOOT_RE.search(text)
    if lt:
        return {"t": t, "kind": "loot", "item": lt.group(1), "count": max(1, _i(lt.group(2), 1))}
    if _MONEY_CTX_RE.search(text):
        c = parse_money(text)
        if c > 0:
            return {"t": t, "kind": "money", "copper": c}
    return None


@dataclass
class ChatSession:
    """Live session from the chat log: XP, money, kills, loot, level-ups, deaths, current zone."""
    start: float | None = None
    last: float | None = None
    xp: int = 0
    copper: int = 0
    kills: int = 0
    deaths: int = 0
    levels: list = field(default_factory=list)
    loot: dict = field(default_factory=dict)
    zone: str = ""
    buckets: dict = field(default_factory=dict)       # bucket index -> [xp, copper]
    events: int = 0
    marks: list = field(default_factory=list)         # (t, kind, value) of xp / level / death, newest last

    def feed(self, events: Iterable[dict]) -> int:
        n = 0
        for ev in events:
            if not isinstance(ev, dict):
                continue
            t = _f(ev.get("t"))
            if t is None:
                continue
            if self.last is not None and t - self.last > SESSION_GAP_S:
                zone = self.zone
                self.__init__()                       # a new play session: start the counters again
                self.zone = zone
            if self.start is None:
                self.start = t
            self.last = max(self.last or t, t)
            self.events += 1
            n += 1
            k = ev.get("kind")
            b = self.buckets.setdefault(int((t - self.start) // BUCKET_S), [0, 0])
            if k == "xp":
                self.xp += _i(ev.get("xp"))
                b[0] += _i(ev.get("xp"))
                self.kills += 1 if ev.get("kill") else 0
                self.marks.append((t, "xp", _i(ev.get("xp"))))
            elif k == "money":
                self.copper += _i(ev.get("copper"))
                b[1] += _i(ev.get("copper"))
            elif k == "loot":
                name = str(ev.get("item") or "?")
                self.loot[name] = self.loot.get(name, 0) + max(1, _i(ev.get("count"), 1))
            elif k == "level":
                self.levels.append(_i(ev.get("level")))
                self.marks.append((t, "level", _i(ev.get("level"))))
            elif k == "death":
                self.deaths += 1
                self.marks.append((t, "death", 1))
            elif k == "zone" and ev.get("zone"):
                self.zone = str(ev["zone"])
        if len(self.marks) > 5000:
            del self.marks[:-4000]
        return n

    def since(self, t0: Any) -> dict:
        """What the chat log saw AFTER ``t0`` (the addon's last save): XP, level-ups, deaths."""
        t0 = _f(t0)
        out = {"xp": 0, "levels": [], "deaths": 0}
        if t0 is None:
            return out
        for t, kind, v in self.marks:
            if t <= t0:
                continue
            if kind == "xp":
                out["xp"] += v
            elif kind == "level":
                out["levels"].append(v)
            elif kind == "death":
                out["deaths"] += 1
        return out

    def elapsed(self) -> float:
        if self.start is None or self.last is None:
            return 0.0
        return max(0.0, self.last - self.start)

    def per_hour(self, total: int) -> int | None:
        el = self.elapsed()
        if el < 120:
            return None
        return int(total * 3600 / max(el, 300))

    def series(self, idx: int) -> list[float]:
        """Cumulative curve per 5-min bucket (idx 0 = XP, 1 = copper)."""
        if not self.buckets:
            return []
        out, acc = [], 0
        for b in range(0, max(self.buckets) + 1):
            acc += self.buckets.get(b, [0, 0])[idx]
            out.append(float(acc))
        return out

    def to_dict(self) -> dict:
        return {"start": self.start, "last": self.last, "duration": self.elapsed(), "xp": self.xp,
                "copper": self.copper, "kills": self.kills, "deaths": self.deaths, "levels": list(self.levels),
                "loot": sorted(self.loot.items(), key=lambda kv: -kv[1])[:8], "zone": self.zone,
                "xp_h": self.per_hour(self.xp), "gold_h": self.per_hour(self.copper),
                "xp_series": self.series(0), "gold_series": self.series(1), "events": self.events}


# ------------------------------------------------------------------------------------- TreeAIDB
def pick_char(db: Any) -> tuple[str, dict]:
    """(``Nom-Royaume``, char entry) of the most recently saved character, ("", {}) if none."""
    chars = _d(_d(db).get("chars"))
    best, best_t = ("", {}), -1.0
    for k, v in chars.items():
        if not isinstance(v, dict):
            continue
        t = _f(_d(v.get("summary")).get("updated")) or 0.0
        if t > best_t:
            best, best_t = (str(k), v), t
    return best


def char_summary(db: Any) -> dict:
    return _d(pick_char(db)[1].get("summary"))


def realm_of(char_key: str) -> str:
    return char_key.split("-", 1)[1] if "-" in char_key else ""


#: (4.0 QA) The addon module that writes each block of the summary. A module that is off (mode sûr, « Plus →
#: Modules ») never rewrites its block and Core keeps the old one: the page would show days-old facts as current.
SUMMARY_OWNERS = {"stats": "Stats", "auction": "Auction", "fight": "Fight", "session": "Session", "dungeon": "Dungeon",
                  "group": "Group", "missions": "Group", "team": "Group", "abandon": "Abandon", "hunter": "Hunter",
                  "boost": "Boost", "errands": "Errands", "gear": "Gear", "talents": "Talents", "prof": "Prof",
                  "pro": "Pro", "leveling": "Leveling", "inventory": "Inventory", "binds": "Binds",
                  "quests": "Quests", "tracker": "Tracker", "todo": "Todo", "compass": "Compass", "plan": "Planner",
                  "detect": "Detect", "navTime": "NavTime"}
#: Blocks written with their own time (epoch, refreshed at every save of a running module): more than
#: ``BLOCK_STALE_S`` older than the summary = a module out of its gate (profil essentiel) or stopped since.
BLOCK_TIME = {"stats": "t", "hunter": "t", "auction": "updated", "group": "updated", "missions": "updated",
              "session": "updated", "fight": "updated", "dungeon": "updated", "abandon": "updated"}


def module_off(db: Any, char: Any, name: str) -> bool:
    """True when the addon module ``name`` (or all of TreeAI) is off for this character: per-character profile
    (``chars[key].moduleSettings``) first, else the account's ``modules`` (same rule as Core's moduleStore)."""
    db, char = _d(db), _d(char)
    per_char = char.get("profile") == "char"
    st = _d(_d(char.get("moduleSettings")).get(name)) if per_char else {}
    if not st:
        st = _d(_d(db.get("modules")).get(name))
    settings = _d(char.get("settings")) if per_char and isinstance(char.get("settings"), dict) else _d(db.get("settings"))
    return st.get("enabled") is False or settings.get("enabled") is False


def group_ok(s: Any) -> bool:
    """The group blocks describe the CURRENT group: in a group at the save, and written with the save (Group keeps
    the last group's blocks once the roster is empty; Core still rewrites ``inGroup`` / ``groupSize``). A block
    without its own time (older addons) is judged by ``inGroup`` / ``groupSize`` only."""
    s = _d(s)
    if s.get("inGroup") is False or _i(s.get("groupSize"), 2) <= 1:
        return False
    upd = _f(s.get("updated"))
    g_t = _f(_d(s.get("group")).get("updated")) or _f(_d(s.get("missions")).get("updated"))
    return upd is None or g_t is None or g_t >= upd - BLOCK_STALE_S


def recent_death(s: Any, now: Any = None) -> dict:
    """``summary.fight.lastDeath`` when it happened less than ``DEATH_RECENT_S`` ago, else {}."""
    ld = _d(_d(_d(s).get("fight")).get("lastDeath"))
    t = _f(ld.get("time"))
    n = _f(now) or time.time()
    return ld if ld and t is not None and 0 <= n - t <= DEATH_RECENT_S else {}


def trusted_summary(db: Any, char: Any, now: Any = None) -> dict:
    """``char.summary`` without what is no longer true: blocks of modules that are off, blocks written more than
    ``BLOCK_STALE_S`` before the summary, the group blocks out of a group, a last death older than
    ``DEATH_RECENT_S``. A shallow copy: the parsed save is shared by every reader and never changed."""
    s = dict(_d(_d(char).get("summary")))
    upd = _f(s.get("updated"))
    for key, mod in SUMMARY_OWNERS.items():
        if key not in s:
            continue
        t = _f(_d(s[key]).get(BLOCK_TIME[key])) if key in BLOCK_TIME else None
        t = t if t is not None and t > 1e9 else None        # an epoch only (Lua falls back to 0 / GetTime())
        if module_off(db, char, mod) or (upd is not None and t is not None and t < upd - BLOCK_STALE_S):
            del s[key]
    if not group_ok(s):
        for key in ("group", "missions", "team"):
            s.pop(key, None)
    fight = s.get("fight")
    if isinstance(fight, dict) and "lastDeath" in fight and not recent_death(s, now):
        s["fight"] = {k: v for k, v in fight.items() if k != "lastDeath"}
    return s


_DB_CACHE: dict[str, tuple[tuple[int, int], dict, dict]] = {}
_DB_LOCK = threading.Lock()
_PATH_LOCKS: dict[str, threading.Lock] = {}
DB_MAX_BYTES = 256 << 20
# WoW writes the AH module's price history as TreeAIDB.modules.Auction.realms[realm].items (thousands of items x
# 14 days, ~90 % of a big save); only the WoW page needs it, and only for the few items it shows.
_AH_MARKS = (b'\n\t["modules"] = {', b"\n\t}", b'\n\t\t["Auction"] = {', b"\n\t\t}", b'\n\t\t\t["realms"] = {',
             b"\n\t\t\t}", b'\n\t\t\t\t\t["items"] = {', b"\n\t\t\t\t\t}", b'\n\t\t\t\t["')


def _strip_ah_items(data: bytes) -> tuple[bytes, dict[str, bytes]]:
    """``data`` without the bodies of ``modules.Auction.realms[*].items`` (left as empty tables) + those bodies
    by realm name. Plain ``bytes.find`` on WoW's tab layout (no regex over megabytes); an unknown layout is
    returned unchanged (then parsed in full)."""
    mod_o, mod_c, auc_o, auc_c, rea_o, rea_c, it_o, it_c, realm_h = _AH_MARKS
    m = data.find(mod_o)
    if m < 0:
        return data, {}
    m_end = data.find(mod_c, m + 1)
    a = data.find(auc_o, m, m_end if m_end > 0 else len(data))
    if a < 0:
        return data, {}
    a_end = data.find(auc_c, a + 1)
    r = data.find(rea_o, a, a_end if a_end > 0 else len(data))
    if r < 0:
        return data, {}
    r_end = data.find(rea_c, r + 1)
    if r_end < 0:
        return data, {}
    pieces, cut, pos, i = [], {}, 0, r
    while True:
        j = data.find(it_o, i, r_end)
        if j < 0:
            break
        if data[j + len(it_o):j + len(it_o) + 1] == b"}":     # « {} » on one line: nothing to cut
            i = j + 1
            continue
        k = data.find(it_c, j + 1, r_end)
        if k < 0:
            break
        h = data.rfind(realm_h, r, j)
        name = data[h + len(realm_h):data.find(b'"]', h + len(realm_h))].decode("utf-8", "replace") if h >= 0 else ""
        body = j + len(it_o)
        pieces.append(data[pos:body])
        cut[name] = data[body:k]
        pos, i = k, k + 1
    if not cut:
        return data, {}
    pieces.append(data[pos:])
    return b"".join(pieces), cut


def _path_lock(key: str) -> threading.Lock:
    with _DB_LOCK:
        lk = _PATH_LOCKS.get(key)
        if lk is None:
            lk = _PATH_LOCKS[key] = threading.Lock()
        return lk


def read_db_cached(path: Any, name: str = "TreeAIDB") -> dict:
    """``TreeAIDB`` of a SavedVariables file, parsed once per (mtime, size) for every reader. The WoW mode
    (every 30 s), the Accueil card and the WoW page read the same file: a big save used to be parsed by each of
    them, over and over, on threads that compete with the window for Python's lock (the UI then waited up to a
    third of a second per repaint while a 9 MB save was parsed). Now: one parse per save (a second reader waits
    for it), without the AH price history (:func:`ah_items` reads the few items the page shows). Read-only
    result. Never raises."""
    if not path:
        return {}
    key = f"{path}|{name}"
    try:
        st = Path(path).stat()
    except OSError:
        return {}
    sig = (int(st.st_mtime_ns), int(st.st_size))
    with _DB_LOCK:
        hit = _DB_CACHE.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1]
    with _path_lock(key):
        with _DB_LOCK:
            hit = _DB_CACHE.get(key)
        if hit is not None and hit[0] == sig:            # parsed by another reader meanwhile
            return hit[1]
        try:
            from treeaicoach.wow import savedvars  # noqa: PLC0415

            if st.st_size > DB_MAX_BYTES:
                return {}
            data = Path(path).read_bytes()
            lite, cut = _strip_ah_items(data)
            if len(lite) > savedvars.MAX_BYTES:
                return {}
            text = lite.decode("utf-8", "replace")
            db = savedvars.parse(text[1:] if text.startswith("﻿") else text).get(name)
            if isinstance(db, list):
                db = {i + 1: v for i, v in enumerate(db)}
            db = db if isinstance(db, dict) else {}
        except Exception:
            log.debug("read_db_cached failed", exc_info=True)
            db, cut = {}, {}
        with _DB_LOCK:
            _DB_CACHE[key] = (sig, db, cut)
            while len(_DB_CACHE) > 4:
                _DB_CACHE.pop(next(iter(_DB_CACHE)))
        return db


def ah_items(db: dict, realm: str, ids: Iterable[int]) -> dict[int, dict]:
    """The AH price history records (``{v, h}``) of ``ids`` for ``realm``, from a database returned by
    :func:`read_db_cached` (parsed now, only these items) or already in ``db``. Never raises."""
    ids = list(ids)
    rec = _d(_d(_d(_d(_d(db).get("modules")).get("Auction")).get("realms")).get(realm))
    have = _d(rec.get("items"))
    out = {i: have[i] for i in ids if isinstance(have.get(i), dict)}
    missing = [i for i in ids if i not in out]
    if not missing:
        return out
    with _DB_LOCK:
        body = next((c.get(realm) for _sig, d, c in _DB_CACHE.values() if d is db), None)
    if not body:
        return out
    try:
        from treeaicoach.wow import savedvars  # noqa: PLC0415

        for i in missing:
            at = body.find(b"\n\t\t\t\t\t\t[%d] = " % int(i))
            if at < 0:
                continue
            at = body.find(b"=", at) + 1
            while at < len(body) and body[at] in (32, 9):
                at += 1
            end = _lua_value_end(body, at, len(body))
            v = savedvars.parse("E = " + body[at:end].decode("utf-8", "replace")).get("E")
            if isinstance(v, dict):
                out[i] = v
    except Exception:
        log.debug("ah_items failed", exc_info=True)
    return out


# ------------------------------------------------------------------------------------- Auctionator
_ITEMKEY_RE = re.compile(r"^(?:[a-z]+:)?(\d+)")


def _atr_entry(v: Any) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v[:1] in "{[":
        try:
            x = json.loads(v)
            return x if isinstance(x, dict) else {}
        except ValueError:
            return {}
    return {}


def _day_map(v: Any) -> dict[int, int]:
    out: dict[int, int] = {}
    for k, p in _d(v).items():
        d, price = _i(k, -1), _i(p)
        if d >= 0 and price > 0:
            out[d + AUCTIONATOR_DAY0] = price
    return out


def _atr_row(e: dict) -> dict | None:
    """One Auctionator entry → {"m": last min price, "h": {epoch day: price}} (None when it has no price)."""
    hist = _day_map(e.get("h"))
    low = _day_map(e.get("l"))
    for d, p in low.items():            # daily low when the high is missing
        hist.setdefault(d, p)
    price = _i(e.get("m")) or (hist[max(hist)] if hist else 0)
    return {"m": price, "h": hist} if price > 0 else None


def parse_auctionator(raw: Any) -> dict[str, dict[int, dict]]:
    """``AUCTIONATOR_PRICE_DATABASE`` → {realm key: {itemID: {"m": last min price, "h": {epoch day: price}}}}.
    Accepts table entries and JSON-string entries; unknown shapes are skipped. Never raises."""
    out: dict[str, dict[int, dict]] = {}
    for realm, items in _d(raw).items():
        if not isinstance(items, dict) or str(realm).startswith("__"):
            continue
        rows: dict[int, dict] = {}
        for key, val in items.items():
            m = _ITEMKEY_RE.match(str(key))
            e = _atr_entry(val)
            if not m or not e:
                continue
            item = int(m.group(1))
            row = _atr_row(e)
            if row is not None and item not in rows:
                rows[item] = row
        if rows:
            out[str(realm)] = rows
    return out


# WoW writes SavedVariables one key per line, tab-indented: a realm of the price database is a depth-1 key,
# an item a depth-2 key. Two anchored regexes find them without parsing anything else.
_ATR_VAR_RE = re.compile(rb"(?m)^" + AUCTIONATOR_VAR.encode() + rb"\s*=\s*\{")
_ATR_REALM_RE = re.compile(rb'\n\t\["((?:[^"\\\n]|\\.)*)"\]\s*=\s*\{')
_ATR_ITEM_RE = re.compile(rb'\n\t\t\["(?:[a-z]+:)?(\d+)[^"\n]*"\]\s*=\s*')
_ATR_SCALAR_RE = re.compile(rb"[^,\n}]*")


def _lua_value_end(b: bytes, i: int, limit: int) -> int:
    """End (exclusive) of the Lua literal that starts at ``b[i]`` (string, table or scalar), within ``limit``."""
    n = min(len(b), limit)
    if i >= n:
        return i
    c = b[i]
    if c in (34, 39):                                  # " or '
        j = i + 1
        while j < n:
            ch = b[j]
            if ch == 92:                               # backslash: skip the escaped byte
                j += 2
                continue
            if ch == c:
                return j + 1
            j += 1
        return n
    if c == 123:                                       # {
        depth, j = 0, i
        while j < n:
            ch = b[j]
            if ch in (34, 39):
                j = _lua_value_end(b, j, n)
                continue
            if ch == 123:
                depth += 1
            elif ch == 125:
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        return n
    m = _ATR_SCALAR_RE.match(b, i, n)
    return m.end() if m else i


class AtrIndex:
    """Auctionator's price database, read lazily. The file's bytes are read once per change (no decoding,
    no parsing), the realm sections are found by one anchored regex, the items of ONE realm are indexed on
    demand (one short regex step per item) and only the entries the page shows are parsed. A 20 MB database
    costs ~0.1 s instead of ~3 s of full parse and ~100 MB of Python objects. A file laid out differently
    falls back to the full parser (correct, slower). Use from one thread (the hub's)."""

    def __init__(self) -> None:
        self.data = b""
        self._realms: dict[str, tuple[int, int]] = {}
        self._items: dict[str, dict[int, int]] = {}
        self._cache: dict[tuple[str, int], dict | None] = {}
        self._full: dict[str, dict[int, dict]] | None = None

    def load(self, path: Any) -> int:
        """(Re)read the file; returns the number of realms found. Never raises."""
        self.__init__()
        try:
            p = Path(path) if path else None
            if p is None or not p.is_file() or p.stat().st_size > ATR_MAX_BYTES:
                return 0
            data = p.read_bytes()
        except OSError:
            return 0
        try:
            m = _ATR_VAR_RE.search(data)
            if m is not None:
                # a top-level table closes with « } » in column 0 (bytes.find: no regex over megabytes)
                end = data.find(b"\n}", m.end())
                end = len(data) if end < 0 else end + 1
                heads = [(h.group(1).decode("utf-8", "replace"), h.start(), h.end())
                         for h in _ATR_REALM_RE.finditer(data, m.end() - 1, end)]
                for i, (name, start, body) in enumerate(heads):
                    if name.startswith("__") or name in self._realms:
                        continue
                    stop = heads[i + 1][1] if i + 1 < len(heads) else end
                    self._realms[name] = (body - 1, stop)
            if self._realms:
                self.data = data
                return len(self._realms)
            # unknown layout: the slow, complete parser (only for files it accepts)
            from treeaicoach.wow import savedvars  # noqa: PLC0415

            if len(data) <= savedvars.MAX_BYTES:
                raw = savedvars.parse(data.decode("utf-8", "replace")).get(AUCTIONATOR_VAR)
                self._full = parse_auctionator(raw)
            return len(self._full or {})
        except Exception:
            log.debug("auctionator index failed", exc_info=True)
            self.__init__()
            return 0

    def realm_keys(self) -> list[str]:
        return list(self._full) if self._full is not None else list(self._realms)

    def sizes(self) -> dict[str, int]:
        """Rough size of each realm (bytes of its section, or items): breaks ties between realm names."""
        if self._full is not None:
            return {k: len(v) for k, v in self._full.items()}
        return {k: e - s for k, (s, e) in self._realms.items()}

    def _index(self, realm: str) -> dict[int, int]:
        idx = self._items.get(realm)
        if idx is None:
            idx = {}
            span = self._realms.get(realm)
            if span is not None:
                for m in _ATR_ITEM_RE.finditer(self.data, span[0], span[1]):
                    item = int(m.group(1))
                    if item not in idx:
                        idx[item] = m.end()
            self._items[realm] = idx
        return idx

    def count(self, realm: str) -> int:
        """Items priced for that realm."""
        if self._full is not None:
            return len(self._full.get(realm) or {})
        return len(self._index(realm)) if realm in self._realms else 0

    def entries(self, realm: str, items: Iterable[int]) -> dict[int, dict]:
        """{itemID: {"m", "h"}} for the asked items that the realm has. Never raises."""
        out: dict[int, dict] = {}
        if self._full is not None:
            rows = self._full.get(realm) or {}
            return {i: rows[i] for i in items if i in rows}
        span = self._realms.get(realm)
        if span is None:
            return out
        from treeaicoach.wow import savedvars  # noqa: PLC0415

        idx = self._index(realm)
        for item in items:
            key = (realm, item)
            if key not in self._cache:
                row = None
                at = idx.get(item)
                if at is not None:
                    try:
                        end = _lua_value_end(self.data, at, span[1])
                        text = self.data[at:end].decode("utf-8", "replace")
                        row = _atr_row(_atr_entry(savedvars.parse("E = " + text).get("E")))
                    except Exception:
                        log.debug("auctionator entry %s unreadable", item, exc_info=True)
                self._cache[key] = row
            if self._cache[key] is not None:
                out[item] = self._cache[key]
        return out


def _norm(s: str) -> str:
    return re.sub(r"[\s_'\-]+", "", str(s or "").lower())


def pick_realm(keys: Iterable[str], realm: str = "", faction: str = "", sizes: dict | None = None) -> str:
    """Best matching realm key (« Royaume Alliance », « Royaume-Alliance-N », « Royaume »…)."""
    keys = [str(k) for k in keys]
    if not keys:
        return ""
    r, f = _norm(realm), _norm(faction)

    def score(k: str) -> tuple:
        nk = _norm(k)
        return (bool(r) and nk.startswith(r), bool(f) and f in nk, (sizes or {}).get(k, 0))

    return max(keys, key=score)


def atr_series(adb: dict, realm_key: str, item: int) -> list[tuple[int, int]]:
    rows = _d(adb.get(realm_key))
    e = _d(rows.get(item))
    return sorted(_d(e.get("h")).items())


def ah_realms(db: Any) -> dict:
    return _d(_d(_d(_d(db).get("modules")).get("Auction")).get("realms"))


def treeai_series(db: Any, realm_key: str, item: int, rec: Any = None) -> list[tuple[int, int]]:
    """Our own AH history (``modules.Auction.realms[key].items[id].h``, flat, stride 7) → [(day, med)].
    ``rec``: the item's record when already read (:func:`ah_items`)."""
    rec = _d(rec) if rec is not None else _d(_d(_d(ah_realms(db).get(realm_key)).get("items")).get(item))
    h = _list(rec.get("h"))
    out = []
    for i in range(0, len(h) - 6, 7):
        day, med = _i(h[i]), _i(h[i + 2])
        if med > 0:
            out.append((day, med))
    return sorted(out)[-30:]


class XpTrack:
    """XP/h measured by the app itself between two saves of the addon (every /reload gives the time, the level
    and the XP): a true rate even without /chatlog. Only a span of ``XP_MIN_S`` or more gives a rate."""

    GAP_S = 2 * 3600                       # no save for 2 h: a new play session

    def __init__(self) -> None:
        self.key = ""
        self.samples: list[tuple[float, int, int, int]] = []     # (t, level, xp, xpMax)

    def feed(self, key: str, t: Any, level: Any, xp: Any, xp_max: Any) -> None:
        t, lvl, x, mx = _f(t), _i(level), _i(xp), _i(xp_max)
        if t is None or lvl <= 0 or mx <= 0:
            return
        last = self.samples[-1] if self.samples else None
        if key != self.key or (last and (t - last[0] > self.GAP_S or lvl < last[1]
                                         or (lvl == last[1] and x < last[2]))):
            self.key, self.samples = key, []
            last = None
        if last and t <= last[0]:
            return
        self.samples.append((t, lvl, x, mx))
        del self.samples[:-60]

    @staticmethod
    def _gain(a: tuple, b: tuple) -> int:
        if b[1] == a[1]:
            return max(0, b[2] - a[2])
        return max(0, a[3] - a[2]) + b[2] + max(0, b[1] - a[1] - 1) * a[3]

    def rate(self) -> dict | None:
        """{xp_h, minutes, samples} over the kept saves, None below ``XP_MIN_S``."""
        if len(self.samples) < 2:
            return None
        span = self.samples[-1][0] - self.samples[0][0]
        if span < XP_MIN_S:
            return None
        gained = sum(self._gain(a, b) for a, b in zip(self.samples, self.samples[1:]))
        return {"xp_h": int(gained * 3600 / span), "minutes": span / 60, "samples": len(self.samples)}

    def points(self) -> list[tuple[float, float]]:
        """Cumulative XP at each save, minutes from the first one (the Session curve without /chatlog)."""
        if len(self.samples) < 2:
            return []
        t0, acc, out = self.samples[0][0], 0, [(0.0, 0.0)]
        for a, b in zip(self.samples, self.samples[1:]):
            acc += self._gain(a, b)
            out.append(((b[0] - t0) / 60, float(acc)))
        return out


# ------------------------------------------------------------------------------------- the hub
class LiveHub:
    """Watches the four files, re-reads only what changed, keeps the latest snapshot.

    Call :meth:`poll` from ONE worker thread (the page's job); :meth:`snapshot` is safe from any thread."""

    def __init__(self, savedvars: str | Path | None = None, auctionator: str | Path | None = None,
                 combat_log: str | Path | None = None, chat_log: str | Path | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._listeners: list[Callable[[list[LiveEvent]], None]] = []
        self.db: dict = {}
        self.adb: dict = {}
        self.atr_index = AtrIndex()
        self.xp_track = XpTrack()
        self.chat = ChatSession()
        self.report: Any = None
        self._paths: tuple | None = None
        self._snap: dict = {}
        self.set_paths(savedvars, auctionator, combat_log, chat_log)

    # --- wiring -----------------------------------------------------------------------------
    def set_paths(self, savedvars: Any = None, auctionator: Any = None, combat_log: Any = None,
                  chat_log: Any = None) -> None:
        paths = tuple(str(p) if p else "" for p in (savedvars, auctionator, combat_log, chat_log))
        if paths == self._paths:
            return
        old = self._paths or ("\0",) * 4
        self._paths = paths
        if paths[0] != old[0]:
            self.sv, self.db = FileWatch(paths[0] or None), {}
        if paths[1] != old[1]:
            self.atr, self.adb = FileWatch(paths[1] or None), {}
            self.atr_index = AtrIndex()
        if paths[2] != old[2]:
            self.combat = self._tail(paths[2])
            self.report = None
        if paths[3] != old[3]:
            self.chat_tail = self._tail(paths[3], CHAT_MAX_BYTES)
            self.chat_watch = FileWatch(paths[3] or None)
            self.chat = ChatSession()

    @staticmethod
    def _tail(path: str, max_bytes: int = 4 << 20) -> Any:
        """Combat log: from its end (old fights are not replayed). Chat log: its last ``CHAT_MAX_BYTES``
        (enough for this play session; the session-gap rule drops older ones)."""
        if not path:
            return None
        from treeaicoach.wow.combatlog import CombatLogTail  # noqa: PLC0415

        chat = max_bytes == CHAT_MAX_BYTES
        tail = CombatLogTail(path, from_start=chat, max_bytes=max_bytes)
        if chat:
            try:
                st = Path(path).stat()
                tail._pos, tail._ident = max(0, st.st_size - CHAT_MAX_BYTES), (st.st_ino, st.st_dev)
                tail.from_start = True
            except OSError:
                pass
        return tail

    def attach_install(self, inst: Any) -> None:
        """Paths from a :class:`treeaicoach.wow.install.WowInstall` (None: forget everything)."""
        if inst is None:
            self.set_paths()
            return
        from treeaicoach.wow import install  # noqa: PLC0415

        sv = install.savedvars_files(inst)
        atr = install.savedvars_files(inst, "Auctionator")
        self.set_paths(sv[0] if sv else None, atr[0] if atr else None, inst.combat_log, inst.chat_log)

    def subscribe(self, fn: Callable[[list[LiveEvent]], None]) -> None:
        self._listeners.append(fn)

    # --- polling ----------------------------------------------------------------------------
    def poll(self) -> list[LiveEvent]:
        """One cheap tick: stat each file, parse what changed. Returns the change events. Never raises."""
        events: list[LiveEvent] = []
        try:
            events += self._poll_savedvars()
            events += self._poll_auctionator()
            events += self._poll_chat()
            events += self._poll_combat()
        except Exception:
            log.debug("live poll failed", exc_info=True)
        snap = self._build_snapshot()
        with self._lock:
            self._snap = snap
        if events:
            for fn in list(self._listeners):
                try:
                    fn(events)
                except Exception:
                    log.debug("live listener failed", exc_info=True)
        return events

    def _poll_savedvars(self) -> list[LiveEvent]:
        if not self.sv.check():
            return []
        self.db = read_db_cached(self.sv.path) if self.sv.sig else {}
        if self.db:
            from treeaicoach.wow import errlog  # noqa: PLC0415

            errlog.write_report(self.db)  # « TreeAI-erreurs.txt » (never raises)
            key, ch = pick_char(self.db)
            s = _d(ch.get("summary"))
            self.xp_track.feed(key, _f(s.get("updated")) or self.sv.mtime, s.get("level"), s.get("xp"), s.get("xpMax"))
        return [LiveEvent("savedvars", "Sauvegarde de l'addon relue" if self.db else "Sauvegarde absente")]

    def _poll_auctionator(self) -> list[LiveEvent]:
        if not self.atr.check():
            return []
        n = self.atr_index.load(self.atr.path) if self.atr.sig else 0
        if not self.atr.sig:
            self.atr_index = AtrIndex()
        self.adb = {}
        return [LiveEvent("auctionator", f"{n} royaume(s) dans Auctionator", n)]

    def _atr_for(self, char_key: str, summary: dict) -> tuple[dict, str, int, dict]:
        """For the items the HV tab shows: Auctionator's prices ({realm: {item: row}}), the realm used and its
        size, and our own AH history records ({item: record}, read lazily from the save)."""
        try:
            a = _d(summary.get("auction"))
            need = {_i(d.get("id")) for d in _list(a.get("deals")) + _list(a.get("owned")) if isinstance(d, dict)}
            need.discard(0)
            realms = ah_realms(self.db)
            our = pick_realm(realms.keys(), realm_of(char_key), sizes={k: _i(_d(v).get("n")) for k, v in realms.items()})
            hist = ah_items(self.db, our, sorted(need)) if our and need else {}
            idx = self.atr_index
            keys = idx.realm_keys()
            if not keys:
                return {}, "", 0, hist
            faction = our.rsplit("-", 1)[1] if "-" in our else ""        # « Royaume-Faction », realms with « - » too
            atr_key = pick_realm(keys, realm_of(char_key), faction, idx.sizes())
            return {atr_key: idx.entries(atr_key, sorted(need))}, atr_key, idx.count(atr_key), hist
        except Exception:
            log.debug("auctionator lookup failed", exc_info=True)
            return {}, "", 0, {}

    def _poll_chat(self) -> list[LiveEvent]:
        if self.chat_tail is None:
            return []
        self.chat_watch.check()
        evs: list[dict] = []
        for _ in range(4):                       # a burst bigger than one read: catch up, bounded
            lines = self.chat_tail.read_lines()
            if not lines:
                break
            evs += [e for e in (parse_chat_line(ln) for ln in lines) if e]
        n = self.chat.feed(evs)
        return [LiveEvent("chat", f"{n} évènements du journal de discussion", evs)] if n else []

    def _poll_combat(self) -> list[LiveEvent]:
        if self.combat is None:
            return []
        evs = self.combat.poll()
        if not evs:
            if self.report is not None:
                self.report.flush(self.clock())
            return []
        if self.report is None:
            from treeaicoach.wow.report import SessionReport  # noqa: PLC0415

            self.report = SessionReport()
        ended = self.report.feed(evs)
        return [LiveEvent("combat", f"{len(evs)} évènements de combat", len(ended))]

    # --- snapshot ---------------------------------------------------------------------------
    def _fights(self) -> dict:
        rep = self.report
        if rep is None:
            return {}
        try:
            fights = rep.all_fights()
            last = fights[-1] if fights else None
            deaths = sum(1 for f in fights for d in f.deaths if d.who == "me")
            return {"count": len(fights), "deaths": deaths,
                    "last": last.to_dict() if last else None, "last_text": last.summary_fr() if last else "",
                    "last_end": last.end if last else None}
        except Exception:
            log.debug("live fights failed", exc_info=True)
            return {}

    def _build_snapshot(self) -> dict:
        now = self.clock()
        key, ch = pick_char(self.db)
        combat_age = None
        if self.combat is not None:
            try:
                combat_age = max(0.0, now - Path(self._paths[2]).stat().st_mtime)
            except OSError:
                combat_age = None
        summary = trusted_summary(self.db, ch, now)
        atr, atr_key, atr_count, ah_hist = self._atr_for(key, summary)
        self.adb = atr
        saved_t = _f(summary.get("updated")) or self.sv.mtime
        return {"now": now, "db": self.db, "char_key": key, "char": ch, "summary": summary,
                "sv_age": self.sv.age(now), "has_sv": bool(self.sv.sig),
                "atr": atr, "atr_key": atr_key, "atr_count": atr_count, "atr_age": self.atr.age(now), "ah_hist": ah_hist,
                "chat": self.chat.to_dict(), "chat_age": self.chat_watch.age(now) if self.chat_tail else None,
                "chat_since": self.chat.since(saved_t), "xp_track": self.xp_track.rate(),
                "xp_points": self.xp_track.points(), "fights": self._fights(), "combat_age": combat_age}

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._snap)


# ------------------------------------------------------------------------------------- views
def _live(age: Any) -> bool:
    return isinstance(age, (int, float)) and age < LIVE_WINDOW_S


def log_quests(s: dict) -> dict[int, dict] | None:
    """{questID: entry} of the quest log the addon saved (``summary.quests.list``); None when unknown."""
    q = _d(_d(s).get("quests"))
    if "list" not in q:
        return None
    return {_i(e.get("id")): e for e in _list(q.get("list")) if isinstance(e, dict) and _i(e.get("id"))}


def log_titles(s: dict) -> set[str] | None:
    logq = log_quests(s)
    return None if logq is None else {str(e.get("title") or "") for e in logq.values()}


def abandon_advice(s: dict, group_size: int = 1) -> list[dict]:
    """Quests worth dropping, each with WHY: the addon's own verdicts only (``summary.abandon`` =
    ``{list = {{title, verdict, why…}}}`` or a plain list). (4.0) The app no longer guesses with its own thresholds
    (the audit: a 3rd copy of the logic, irreversible advice on numbers we do not have). (4.0 QA) The addon's list
    also holds its « later » verdicts (« fais-la si tu passes à côté »): only ``verdict == "abandon"`` (or no
    verdict: older saves) becomes « Retire ». Quests no longer in the log are dropped. ``group_size`` is kept for
    callers."""
    a = s.get("abandon")
    own = _list(a.get("list")) if isinstance(a, dict) else _list(a)
    titles = log_titles(s)
    out = []
    for e in own[:10]:
        if isinstance(e, dict):
            if str(e.get("verdict") or "abandon") != "abandon":       # « later » = do it if you pass by
                continue
            title = str(e.get("title") or e.get("q") or "").strip()
            why = str(e.get("why") or e.get("reason") or "").strip() or "elle ne vaut plus le coup"
        else:
            title, why = str(e or "").strip(), "conseillé par l'addon"
        if title and (titles is None or title in titles):
            out.append({"title": title, "why": why})
    return out


#: The 5 status words of a quest (same as the addon, Modules/Plain.lua) — never XP/min, score or %.
QUEST_TAGS = ("Prête à rendre", "À retirer", "À plusieurs", "Rapide", "Long")
#: Words that must never reach the main quest UI (robustness check, see tests).
JARGON = ("score", "λ", "/100", "coefficient")   # internal jargon only: XP/min, XP/h, % and minutes are wanted


def is_plain(text: str) -> bool:
    """True when a main-UI quest line speaks plain French: no XP/min, score, λ, cost… and « % » only for a
    progress (« niveau 27 : 62 % »)."""
    low = str(text or "").lower()
    if any(w in low for w in JARGON):
        return False
    return True


def quest_tag(r: dict, drop: bool = False) -> tuple[str, str]:
    """(one status word, one short reason) for a tracker route entry; the addon's own tag wins."""
    tag, why = str(r.get("tag") or ""), str(r.get("why") or "")
    if tag in QUEST_TAGS:
        return tag, why
    sec = _i(r.get("sec"))
    mins = f"≈ {max(1, round(sec / 60))} min" if sec else ""
    if r.get("ready"):
        return "Prête à rendre", "Tu as tout fait : il ne reste qu'à la rendre."
    if drop:
        return "À retirer", "Elle ne vaut plus le coup."
    if r.get("hard"):
        return "À plusieurs", "Trop dure seul : fais-la avec tes potes."
    if sec and sec <= 600:
        return "Rapide", f"Vite faite ({mins})."
    return "Long", f"Elle prend du temps ({mins})." if mins else "Elle prend du temps."



def quests_view(snap: dict) -> dict:
    """« Quêtes » tab in plain French: « À faire maintenant » (one sentence + why), the quests best first with ONE
    status word each (Rapide / Long / À plusieurs / Prête à rendre / À retirer), the plan, the quests to drop."""
    s = _d(snap.get("summary"))
    tr, qs = _d(s.get("tracker")), _d(s.get("quests"))
    drop = {a["title"] for a in abandon_advice(s)}
    route = []
    for r in _list(tr.get("route"))[:8]:
        if not isinstance(r, dict):
            continue
        title = str(r.get("title") or "?")
        tag, why = quest_tag(r, title in drop)
        route.append((title, tag, why))
    head = []
    if qs.get("count") is not None:
        n, ready = _i(qs.get("count")), _i(qs.get("complete"))
        head.append(f"{n} quête{'s' if n > 1 else ''}" + (f", {ready} prête{'s' if ready > 1 else ''} à rendre" if ready else ""))
    if tr.get("totalMin"):
        head.append(f"tout finir ≈ {duration_text(_i(tr['totalMin']) * 60)}")
    tips = []
    if isinstance(tr.get("pickup"), dict):
        p = tr["pickup"]
        tips.append(f"En chemin : prends « {p.get('title')} »" + (f" chez {p['giver']}" if p.get("giver") else ""))
    for e in _list(tr.get("easyNearby"))[:3]:
        if isinstance(e, dict):
            tips.append(f"Facile, juste à côté : « {e.get('title')} »")
    if qs.get("next"):
        tips.append(str(qs["next"]))
    plan: list[str] = []
    from treeaicoach.wow import call  # noqa: PLC0415

    res = call("planner", "plan_lines", snap.get("db") or {}, default=None)
    if isinstance(res, (list, tuple)):
        plan = [str(x) for x in res if x][:8]
    if not plan and tr.get("plan"):
        plan = ["Un plan TreeAI est actif en jeu (en tête du suivi de quêtes)."]
    td = _d(s.get("todo"))
    now = _d(td.get("now"))
    now_text, now_why = str(now.get("text") or ""), str(now.get("why") or "")
    if not now_text:
        first = next((r for r in _list(tr.get("route")) if isinstance(r, dict)), None)
        if first:
            now_text = str(first.get("task") or (f"Rends « {first.get('title')} »" if first.get("ready")
                                                 else f"Fais « {first.get('title')} »"))
            now_why = "C'est la quête la plus rapide autour de toi."
    return {"head": " · ".join(head), "now": now_text, "why": now_why, "route": route, "tips": tips, "plan": plan,
            "abandon": abandon_advice(s), "empty": not (tr or qs)}


def group_view(snap: dict) -> dict:
    """« Groupe » tab: the missions board of the 4 (summary.missions), only for the current group."""
    s = _d(snap.get("summary"))
    m = _d(s.get("missions")) if group_ok(s) else {}
    members = [f"{x.get('name')} ({x.get('class') or '?'} {_i(x.get('level'))})"
               for x in _list(m.get("members")) if isinstance(x, dict)]
    board = []
    for e in _list(m.get("board"))[:10]:
        if not isinstance(e, dict):
            continue
        holders = _i(e.get("holders"))
        if e.get("ready"):
            state = "tous prêts à rendre"
        else:
            state = str(e.get("status") or f"{_i(e.get('done'))}/{holders} ont fini")
        pct = _i(e.get("pct"))
        board.append({"title": str(e.get("title") or "?"), "state": state, "pct": max(0, min(100, pct)),
                      "holders": holders, "missing": _i(e.get("missing"))})
    claims = [f"{c.get('by')} s'occupe de « {c.get('quest')} »" for c in _list(m.get("claims")) if isinstance(c, dict)]
    fresh = (data_age(snap) or 0) < STALE_S           # « Rejoins le groupe : ils sont dans … » of hours ago: no
    return {"members": members, "board": board, "next": _plain(m.get("next")),
            "assign": [str(x) for x in _list(m.get("assign"))][:6], "claims": claims[:6],
            "travel": [str(x) for x in _list(m.get("travel"))][:5] if fresh else [],
            "crafts": [str(x) for x in _list(m.get("crafts"))][:6],
            "no_addon": _i(m.get("noAddon")), "empty": not m}


def _deal_net(d: dict, neutral: bool) -> dict:
    from treeaicoach.wow import auction  # noqa: PLC0415

    buy, sell = _i(d.get("buy")), _i(d.get("sell"))
    if d.get("kind") == "vendor":
        return {"cut": 0, "deposit": 0, "net": sell - buy, "risk": 0}
    cut = auction.ah_cut(sell, neutral)
    dep = _i(d.get("deposit"))
    return {"cut": cut, "deposit": dep, "net": sell - cut - buy, "risk": dep}


def ah_view(snap: dict) -> dict:
    """« HV » tab: deals with the net profit after commission (+ deposit at risk), my auctions,
    realized profit (+ daily curve from the ledger), price history from our DB and Auctionator's."""
    from treeaicoach.wow import auction  # noqa: PLC0415

    s = _d(snap.get("summary"))
    a = _d(s.get("auction"))
    db, adb = _d(snap.get("db")), _d(snap.get("atr"))
    neutral = bool(a.get("neutral"))
    key = str(snap.get("char_key") or "")
    realms = ah_realms(db)
    our_key = pick_realm(realms.keys(), realm_of(key), sizes={k: _i(_d(v).get("n")) for k, v in realms.items()})
    faction = our_key.rsplit("-", 1)[1] if "-" in our_key else ""
    atr_key = pick_realm(adb.keys(), realm_of(key), faction, {k: len(v) for k, v in adb.items()})
    hist = _d(snap.get("ah_hist"))
    deals = []
    for d in _list(a.get("deals"))[:6]:
        if not isinstance(d, dict):
            continue
        item = _i(d.get("id"))
        n = _deal_net(d, neutral)
        atr_price = _i(_d(_d(adb.get(atr_key)).get(item)).get("m")) if item else 0
        deals.append({"name": str(d.get("name") or f"Objet {item}"), "id": item, "kind": d.get("kind") or "flip",
                      "count": max(1, _i(d.get("count"), 1)), "buy": _i(d.get("buy")), "sell": _i(d.get("sell")),
                      "expected": _i(d.get("profit")), "roi": _f(d.get("roi")), "conf": _f(d.get("conf")),
                      "atr": atr_price, **n, "sold": _f(d.get("sold")), "stop": _i(d.get("stop")),
                      "max_qty": _i(d.get("maxQty")),
                      "ours": treeai_series(db, our_key, item, hist.get(item)) if item else [],
                      "atr_series": atr_series(adb, atr_key, item) if item else []})
    mine = []
    for o in _list(a.get("owned"))[:10]:
        if isinstance(o, dict):
            mine.append({"name": str(o.get("name") or f"Objet {_i(o.get('id'))}"), "count": max(1, _i(o.get("count"), 1)),
                         "unit": _i(o.get("unit")), "lowest": _i(o.get("lowest")), "undercut": bool(o.get("undercut")),
                         "advice": str(o.get("advice") or "")})
    ledger = _list(_d(_d(_d(snap.get("char")).get("modules")).get("Auction")).get("ledger"))
    recent = [e for e in ledger if isinstance(e, dict) and e.get("type") == "sell"][-5:][::-1]
    rp = auction.realized_profit([e for e in ledger if isinstance(e, dict)])
    days = sorted((k, v) for k, v in rp["days"].items() if k)[-14:]
    r = _d(a.get("realized"))
    if not r and rp["days"]:                 # no (fresh) AH block: today / 7 days from the ledger's own days
        n = time.localtime(_f(snap.get("now")) or time.time())
        keys = {time.strftime("%Y-%m-%d", time.localtime(time.mktime((n.tm_year, n.tm_mon, n.tm_mday - k, 12, 0, 0,
                                                                         0, 0, -1)))): k for k in range(7)}
        r = {"today": sum(v for k, v in rp["days"].items() if keys.get(k) == 0),
             "week": sum(v for k, v in rp["days"].items() if k in keys)}
    realized = {"today": _i(r.get("today")), "week": _i(r.get("week")), "total": _i(r.get("total", rp["total"])),
                "top": [(str(t.get("item")), _i(t.get("profit"))) for t in _list(r.get("top")) if isinstance(t, dict)][:5],
                "series": [float(v) for _k, v in days]}
    info = []
    scan = auction.scan_age(a, data_age(snap), db, snap.get("now"))     # (shown in the tab's freshness line)
    info.append("HV neutre : commission 15 %" if neutral else "commission 5 %")
    n_atr = _i(snap.get("atr_count")) or sum(len(v) for v in adb.values())
    if n_atr:
        info.append(f"Auctionator : {fmt_int(n_atr)} prix ({atr_key or '?'})")
    # (4.0) the audit: deals of a scan older than 24 h (or of no known scan) are not shown as buyable
    stale_deals = 0
    if deals and (scan is None or scan > AH_MAX_AGE_S):
        stale_deals, deals = len(deals), []
    gold = _d(a.get("gold"))
    budget = ""
    if gold:
        budget = (f"Or {money(gold.get('total'))} · réserve {money(gold.get('reserve'))} · "
                  f"immobilisé {money(gold.get('invested'))}" + (f" · budget restant {money(a['budgetLeft'])}"
                                                                 if a.get("budgetLeft") is not None else ""))
    none: list[str] = []
    if stale_deals:
        none = [auction.STALE_TEXT, ("scan trop ancien" if scan is not None else "aucun scan connu")
                + " : ouvre l'HV, clique « Scanner », puis /reload"]
    elif not deals and isinstance(a.get("none"), dict):
        none = [auction.NONE_TEXT] + [str(x) for x in _list(a["none"].get("why"))][:4]
    rules = _d(a.get("rules"))
    rules_t = (f"Règles : jamais plus de {_i(rules.get('capGold'), 20)} % de ton or dans un objet, quantité ≤ ce qui "
               f"s'est vendu en {_i(rules.get('qtyDays'), 3)} j, stop-loss −15 %.") if rules else ""
    return {"deals": deals, "none": none, "rules": rules_t, "mine": mine, "mine_known": "owned" in a,
            "scan_age": scan, "stale_deals": stale_deals,
            "recent": [(str(e.get("item") or "?"), _i(e.get("money")) - _i(e.get("cut")),
                        day_text(e.get("day"), snap.get("now"))) for e in recent],
            "realized": realized, "info": " · ".join(info), "budget": budget,
            "thin": [str(x) for x in _list(a.get("thin"))][:5], "empty": not a and not adb}


def session_view(snap: dict) -> dict:
    """« Session » tab: recap of the play session (chat log live + missions session) and goals."""
    s = _d(snap.get("summary"))
    chat = _d(snap.get("chat"))
    ses = _d(_d(s.get("missions")).get("session")) if group_ok(s) else {}
    rows: list[tuple[str, str]] = []
    if chat.get("events"):
        # (4.0) a chat log not written for 10 min is the LAST session, never « en direct »
        src = "en direct" if _live(snap.get("chat_age")) else \
            ("journal de discussion " + age_text(snap.get("chat_age"))).strip()
        rows.append((f"Durée ({src})", duration_text(chat.get("duration")) or "< 1 min"))
        rows.append(("XP gagnée", f"{_i(chat.get('xp')):,}".replace(",", " ") +
                     (f" · {_i(chat.get('kills'))} monstres" if chat.get("kills") else "")))
        if chat.get("copper"):
            rows.append(("Or ramassé", money(chat.get("copper"))))
        if chat.get("levels"):
            rows.append(("Niveaux", ", ".join(str(x) for x in chat["levels"])))
    if ses:
        nt, nd = _i(ses.get("turned")), _i(ses.get("deaths"))      # (Group writes missions = 0: never shown)
        rows.append(("Session du groupe", f"{duration_text(ses.get('duration')) or '?'} · {nt} quête{'s' if nt > 1 else ''} "
                                          f"rendue{'s' if nt > 1 else ''} · {nd} mort{'s' if nd > 1 else ''}"))
        for lv in _list(ses.get("levels"))[:4]:
            if isinstance(lv, dict) and _i(lv.get("to")) > _i(lv.get("from")):
                rows.append((str(lv.get("name")), f"niveau {_i(lv.get('from'))} → {_i(lv.get('to'))}"))
    my = _d(s.get("session"))          # (4.0) module « Ma session » : sa durée au /reload (Économie / Prévisions retirés)
    if _i(my.get("minutes")) > 0 and not chat.get("events"):
        rows.append(("Durée (au /reload)", duration_text(_i(my["minutes"]) * 60)))
    goals = []
    g = _d(ses.get("goal"))
    if g:
        txt = _plain(g.get("text") or g.get("kind") or "Objectif")
        eta = f" · ~{duration_text(g.get('eta'))}" if _i(g.get("eta")) > 0 else ""
        goals.append({"text": txt + (" : atteint !" if g.get("done") else eta), "pct": max(0, min(100, _i(g.get("pct"))))})
    lp = level_progress(snap)
    if lp and lp.get("pct") is not None:
        # (4.0) the time comes from a MEASURED XP/h (xp_rate), never from an unmeasured estimate
        goals.append({"text": lp["text"] + (f" ({lp['conf']})" if lp.get("conf") else ""), "pct": lp["pct"]})
    q = _d(s.get("quests"))
    if _i(q.get("questsToLevel")) > 0:
        n = _i(q["questsToLevel"])
        goals.append({"text": f"~{n} quête{'s' if n > 1 else ''} pour le niveau suivant", "pct": None})
    # (4.0) module Groupe : XP gagnée pendant la session du groupe (plus d'XP/h estimée par membre)
    board = [(str(e.get("name")), _i(e.get("xp"))) for e in _list(ses.get("leaderboard")) if isinstance(e, dict)]
    events = [str(x) for x in _list(ses.get("events"))][:6]
    return {"rows": rows, "goals": goals, "leaderboard": board, "events": events,
            "empty": not rows and not goals}


# ------------------------------------------------------------------------------------- 4.0: truth first
def data_age(snap: dict) -> float | None:
    """Seconds since the addon captured what the page shows: ``summary.updated`` (written at the save) when it
    is sane, never younger than the file itself; None when there is no save."""
    snap = _d(snap)
    s = _d(snap.get("summary"))
    sv_age = snap.get("sv_age")
    sv_age = float(sv_age) if isinstance(sv_age, (int, float)) and not isinstance(sv_age, bool) else None
    now = _f(snap.get("now")) or time.time()
    upd = _f(s.get("updated"))
    if upd is not None and now - 30 * 86400 < upd <= now + 300:
        age = max(0.0, now - upd)
        return max(age, sv_age) if sv_age is not None else age
    return sv_age


def freshness(snap: dict) -> dict:
    """How old the page's data is, in words: {age, text, badge, tone, live, stale, old}. Data older than
    ``STALE_S`` is never presented as current; the chat log (/chatlog) is the only live source."""
    age = data_age(snap)
    chat = _d(_d(snap).get("chat"))
    live = _live(_d(snap).get("chat_age")) and bool(chat.get("events"))
    if age is None:
        text = "Pas encore de sauvegarde de l'addon : connecte-toi puis tape /reload en jeu."
        badge, tone = "Pas de données", ""
    else:
        when = age_text(age)                                 # « à l'instant » under 90 s
        text = f"Données du /reload {when}." + (" Tape /reload pour les rafraîchir." if age >= STALE_S else "")
        badge = "/reload " + when
        tone = "warning" if age >= STALE_S else ""
    if live:
        # (4.0 QA) the chat log makes XP, gold and deaths live, not the step or the quests of a 7-hour-old save
        if age is not None and age < STALE_S:
            badge, tone = "En direct", "ok"
        text += " XP, or, morts et butin : en direct (journal de discussion)."
    return {"age": age, "text": text, "badge": badge, "tone": tone, "live": live,
            "stale": age is None or age >= STALE_S, "old": age is not None and age >= OLD_S}


def _secs(sec: Any) -> str:
    """« 45 s » / « 4 min » / « 1 h 05 » (rounded to the minute above 1 min)."""
    s = _i(sec)
    if s <= 0:
        return ""
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{max(1, round(s / 60))} min"
    m = round(s / 60)
    return f"{m // 60} h {m % 60:02d}"


def fmt_int(n: Any) -> str:
    return f"{_i(n):,}".replace(",", " ")


def _why(v: Any) -> str:
    if isinstance(v, (list, dict)):
        v = " ".join(str(x) for x in _list(v) if x)
    return " ".join(str(v or "").split())


_EM_DASH = chr(0x2014)


def _plain(v: Any) -> str:
    """An addon text for the page: one line, no em dash (« Ensemble : « X », tiret, toi 6/8 » → « … : toi 6/8 »)."""
    return _why(v).replace(f" {_EM_DASH} ", " : ").replace(_EM_DASH, "-")


def day_text(day: Any, now: Any = None) -> str:
    """The addon's ledger day (« 2026-11-05 ») in French: « aujourd'hui », « hier », « 5 nov. »."""
    try:
        y, mo, d = (int(x) for x in str(day or "").split("-"))
        n = time.localtime(_f(now) or time.time())
        delta = (time.mktime((n.tm_year, n.tm_mon, n.tm_mday, 12, 0, 0, 0, 0, -1))
                 - time.mktime((y, mo, d, 12, 0, 0, 0, 0, -1))) / 86400
        if round(delta) == 0:
            return "aujourd'hui"
        if round(delta) == 1:
            return "hier"
        return f"{d} {MOIS[mo - 1]}" + (f" {y}" if y != n.tm_year else "")
    except (ValueError, IndexError, OverflowError):
        return str(day or "")


def _xp_cap(s: dict) -> int:
    """Above this XP/h a number is absurd (more than 4 levels an hour): never shown."""
    return max(20000, 4 * _i(s.get("xpMax")))


def xp_rate(snap: dict) -> dict | None:
    """The best MEASURED XP/h, never a model's: the live chat log, the addon's measured stats, the addon's
    session, or the app's own samples between two /reload. {xp_h, src, minutes, conf, live} or None when
    nothing was measured over 10 min or more (or the number is absurd)."""
    snap = _d(snap)
    s = _d(snap.get("summary"))
    cap = _xp_cap(s)
    chat = _d(snap.get("chat"))
    cands: list[tuple[float, float, str, bool]] = []
    dur = _f(chat.get("duration")) or 0.0
    if _live(snap.get("chat_age")) and dur >= XP_MIN_S and _i(chat.get("xp")) > 0:
        cands.append((_i(chat["xp"]) * 3600 / dur, dur / 60, "en direct", True))
    st = _d(s.get("stats"))
    if st.get("v") == 1:
        # Stats.xph = {q, g, d, all, recent, recentMin, min}: the last hour's rate is measured over recentMin
        # minutes, the overall one over min minutes of play (older saves: played)
        xph = _d(st.get("xph"))
        rv, rmin = _f(xph.get("recent")), _f(xph.get("recentMin")) or 0.0
        av, amin = _f(xph.get("all")), _f(xph.get("min")) or _f(st.get("played")) or 0.0
        if rv and rmin >= XP_MIN_S / 60:
            cands.append((rv, rmin, "mesurée par l'addon", False))
        elif av and amin >= XP_MIN_S / 60:
            cands.append((av, amin, "mesurée par l'addon", False))
    ses = _d(s.get("session"))
    if _i(ses.get("minutes")) >= XP_MIN_S / 60 and _i(ses.get("xp")) > 0:
        cands.append((_i(ses["xp"]) * 60 / _i(ses["minutes"]), float(_i(ses["minutes"])), "session de l'addon", False))
    tr = _d(snap.get("xp_track"))
    if _i(tr.get("xp_h")) > 0 and (_f(tr.get("minutes")) or 0) >= XP_MIN_S / 60:
        cands.append((float(tr["xp_h"]), float(tr["minutes"]), "entre tes /reload", False))
    for v, mins, src, live in cands:
        if 0 < v <= cap:
            conf = "fiable" if mins >= 30 else "approximatif"
            return {"xp_h": int(round(v)), "src": src, "minutes": mins, "conf": conf, "live": live}
    return None


def level_progress(snap: dict, rate: dict | None = None) -> dict | None:
    """Level, % and time to the next level from the save, plus what the live chat log saw since the save.
    {level, next, pct, eta, conf, text} or None without XP data."""
    snap = _d(snap)
    s = _d(snap.get("summary"))
    lvl, xp, mx = _i(s.get("level")), _i(s.get("xp")), _i(s.get("xpMax"))
    if lvl <= 0 or mx <= 0 or xp < 0 or xp > mx:
        return None
    since = _d(snap.get("chat_since")) if _live(snap.get("chat_age")) else {}
    ups = [_i(x) for x in _list(since.get("levels")) if _i(x) > lvl]
    if ups:
        return {"level": max(ups), "next": max(ups) + 1, "pct": None, "eta": "", "conf": "",
                "text": f"Niveau {max(ups)} atteint (en direct)"}
    xp = min(mx, xp + max(0, _i(since.get("xp"))))
    if xp >= mx:                       # the live XP covers the level but no level-up line was read yet
        return {"level": lvl, "next": lvl + 1, "pct": 99, "eta": "imminent", "conf": "",
                "text": f"Niveau {lvl + 1} imminent"}
    pct = int(100 * xp / mx)
    eta, conf = "", ""
    rate = rate if rate is not None else xp_rate(snap)
    if rate and rate.get("xp_h"):
        sec = (mx - xp) * 3600 / rate["xp_h"]
        eta = "plus de 24 h" if sec > 86400 else "~" + _secs(sec)
        conf = rate.get("conf") or ""
    return {"level": lvl, "next": lvl + 1, "pct": pct, "eta": eta, "conf": conf,
            "text": f"Niveau {lvl + 1}" + (f" dans {eta}" if eta else "")}


def danger_facts(snap: dict) -> list[tuple[str, str]]:
    """At most two FACTS worth a glance (deaths, last death's cause, a friend down, full bags), worst first:
    [(text, tone)]. Empty when nothing is known (never « aucun danger » without a source that could tell)."""
    snap = _d(snap)
    s = _d(snap.get("summary"))
    age = data_age(snap) or 0
    fresh = age < STALE_S
    recent = age < VOLATILE_S          # a friend down, full bags: true only for a few minutes
    out: list[tuple[int, str, str]] = []
    chat = _d(snap.get("chat"))
    if _live(snap.get("chat_age")) and chat.get("events"):
        n, dur = _i(chat.get("deaths")), duration_text(chat.get("duration")) or "< 1 min"
        if n:
            out.append((3 if n >= 2 else 2, f"{n} mort{'s' if n > 1 else ''} en {dur} (en direct)",
                        "danger" if n >= 2 else "warning"))
        elif (_f(chat.get("duration")) or 0) >= XP_MIN_S:
            out.append((0, f"Aucune mort depuis {dur}", "ok"))
    if fresh:
        ld = recent_death(s, snap.get("now"))
        if ld:
            who = last_death_text(ld)
            adv = _why(ld.get("advice"))
            out.append((2, "Dernière mort" + (f" : {who}" if who else "") + (f". {adv}" if adv else ""), "warning"))
        ses = _d(s.get("session"))
        if _i(ses.get("deaths")) >= 2 and not any(t.endswith("(en direct)") for _p, t, _c in out):
            tip = _why(ses.get("tip"))
            out.append((2, f"{_i(ses['deaths'])} morts cette session" + (f". {tip}" if tip else ""), "warning"))
        for m in _list(_d(s.get("group")).get("members")) if recent and group_ok(s) else []:
            if isinstance(m, dict) and m.get("dead") and not m.get("me") and m.get("name"):
                out.append((3, f"{m['name']} est mort", "danger"))
        if recent and _i(s.get("bagsTotal")) > 0 and s.get("bagsFree") is not None and _i(s.get("bagsFree")) == 0:
            out.append((1, "Sacs pleins : plus de butin", "warning"))
        run = _d(_d(s.get("dungeon")).get("run"))
        if _d(s.get("dungeon")).get("key") and _i(run.get("wipes")):
            out.append((2, f"{_i(run['wipes'])} wipe{'s' if _i(run['wipes']) > 1 else ''} dans ce donjon", "warning"))
    out.sort(key=lambda x: -x[0])
    return [(t, c) for _p, t, c in out[:2]]


def last_death_text(ld: Any) -> str:
    """« Loup affamé (niv 24, élite) » from the addon's ``summary.fight.lastDeath`` = {killer, level, elite, advice,
    zone, time, duration, startHP} (4.0: facts only, read on the target); "" when the killer is unknown."""
    ld = _d(ld)
    killer = _why(ld.get("killer"))
    if not killer:
        return ""
    tags = [f"niv {_i(ld['level'])}"] if _i(ld.get("level")) > 0 else []
    if ld.get("elite"):
        tags.append("élite")
    return killer + (f" ({', '.join(tags)})" if tags else "")


_TURN_RE = re.compile(r"^(?:Rends|Rendre|Fais|Termine)\b.*?«\s*([^»]+?)\s*»")
_ETA_IN_TEXT = re.compile(r"\s*\(≈[^)]*\)")


def _quest_text_ok(text: str, titles: set[str] | None) -> bool:
    """False when « Rends / Fais « Quête » » names a quest that is not in the log (stale step)."""
    m = _TURN_RE.match(text or "")
    return titles is None or m is None or m.group(1) in titles


def _yd_text(yd: Any) -> str:
    d = _f(yd)
    if d is None or d <= 0 or d > 60000:
        return ""
    m = d * 0.9144
    return f"{int(round(m / 10) * 10) if m >= 100 else int(round(m))} m" if m < 1000 else \
        f"{m / 1000:.1f} km".replace(".", ",")


def next_step(snap: dict) -> dict:
    """The one thing to do now: the addon's « Maintenant » step (Todo), else the plan's step, else the compass
    target, else the best quest of the log. {title, why, dist, eta, src}. Quests that left the log are
    never shown; the compass distance is attached only when it points at that step."""
    s = _d(_d(snap).get("summary"))
    logq, titles = log_quests(s), log_titles(s)
    now = _d(_d(s.get("todo")).get("now"))
    tgt = _d(_d(s.get("compass")).get("target"))
    plan = _d(s.get("plan"))
    out = {"title": "", "why": "", "dist": "", "eta": "", "src": ""}
    timely = (data_age(snap) or 0) < STALE_S           # (4.0 QA) no « ≈ 1 min » on a step of a 7-hour-old save
    text = _why(now.get("text"))
    if not timely:                                     # the addon writes its own « (≈ 1 min) » into the text
        text = _ETA_IN_TEXT.sub("", text).strip()
    if text and _quest_text_ok(text, titles):
        out.update(title=text, why=_why(now.get("why")), src="todo")
        if timely and 0 < _i(now.get("eta")) < 6 * 3600 and "≈" not in text:     # the addon's text may hold it
            out["eta"] = "≈ " + _secs(now.get("eta"))
    elif plan.get("label") and plan.get("src") not in (None, "", "none") and not plan.get("stale") \
            and _quest_text_ok(str(plan["label"]), titles):
        n, tot = _i(plan.get("step")), _i(plan.get("steps"))
        label = _why(plan["label"]) if timely else _ETA_IN_TEXT.sub("", _why(plan["label"])).strip()
        out.update(title=label, why=f"Étape {n} sur {tot} de ton trajet." if n and tot else "", src="plan")
    tq = _i(tgt.get("questID"))
    tgt_ok = bool(tgt) and (not tq or logq is None or tq in logq)
    label = _why(tgt.get("label") or tgt.get("title"))
    if not out["title"] and tgt_ok and label:
        out.update(title=label, why="Cible de ta boussole.", src="compass")
    if not out["title"]:
        for r in _list(_d(s.get("tracker")).get("route")):
            if isinstance(r, dict) and (logq is None or _i(r.get("id")) in logq) and r.get("title"):
                verb = "Rends" if r.get("ready") else "Fais"
                out.update(title=str(r.get("task") or f"{verb} « {r['title']} »"),
                           why="La quête la plus rapide de ton journal.", src="tracker")
                break
    ttl = str(tgt.get("title") or "")
    if tgt_ok and out["title"] and (out["src"] == "compass" or (ttl and ttl in out["title"])
                                    or (label and label in out["title"])
                                    or (out["src"] == "plan" and tgt.get("src") == "plan")):
        out["dist"] = (_why(tgt.get("distText")) or _yd_text(tgt.get("dist"))) if (data_age(snap) or 0) < LIVE_WINDOW_S \
            else ""
        if timely and tgt.get("eta") and not out["eta"] and "≈" not in out["title"]:
            out["eta"] = "≈ " + _why(tgt.get("eta"))
        zone = _why(tgt.get("zone"))
        if zone and zone != str(s.get("zone") or ""):
            out["dist"] = " · ".join(x for x in (zone, out["dist"]) if x)
    return out


def hero_view(snap: dict) -> dict:
    """The « Maintenant » card: who and where, the next step, XP/h, the next level, at most two danger facts,
    all with their freshness. Pure."""
    snap = _d(snap)
    s = _d(snap.get("summary"))
    fresh = freshness(snap)
    key = str(snap.get("char_key") or "")
    name = key.split("-", 1)[0] if key else ""
    cls = str(s.get("className") or s.get("class") or "")
    who = " · ".join(x for x in (name, f"{cls} {s['level']}".strip() if s.get("level") else cls) if x)
    chat = _d(snap.get("chat"))
    zone = (chat.get("zone") if fresh["live"] and chat.get("zone") else "") or str(s.get("zone") or "")
    if s.get("subzone") and zone == s.get("zone"):
        zone += f" · {s['subzone']}"
    step = next_step(snap) if s else {"title": "", "why": "", "dist": "", "eta": "", "src": ""}
    rate = xp_rate(snap) if s else None
    lvl = level_progress(snap, rate) if s else None
    kicker = "Maintenant"
    if fresh["old"]:
        kicker = "Dernière session"
    elif fresh["stale"] and s:
        kicker = "Au dernier /reload"
    return {"empty": not s, "who": who, "zone": zone, "kicker": kicker, "badge": fresh["badge"],
            "badge_tone": fresh["tone"], "fresh": fresh["text"], "stale": fresh["stale"],
            "step": step, "xp": rate, "level": lvl, "danger": danger_facts(snap) if s else []}


STOPS_MAX = 8                             # the next stops only, like the in-game guide
STEP_KIND = {"pick": "Prendre la quête", "obj": "Objectif", "turn": "Rendre la quête", "fly": "Vol",
             "boat": "Bateau", "zep": "Zeppelin", "hs": "Pierre de foyer", "train": "Maître", "vendor": "Marchand",
             "meet": "Regroupement", "grind": "Monstres en attendant la suite", "fp": "Point de vol à apprendre"}


def _map_names(db: Any) -> dict[int, str]:
    return {_i(m.get("id")): str(m.get("name") or "") for m in _list(_d(_d(db).get("export")).get("maps"))
            if isinstance(m, dict) and _i(m.get("id"))}


def trajet_view(snap: dict, plan_file: Any = None) -> dict:
    """« Trajet »: the numbered stops of the route the game follows (the app's plan file + the step the addon
    is at, the game's own next stops, or the quest tracker's order), only quests of the log; what changed since.
    No stop is « current » in a save older than ``STALE_S``. Pure.
    {stops=[{n, title, desc, current, kind}], source, head, warn, dropped, minutes}."""
    out = _trajet_view(snap, plan_file)
    if (data_age(snap) or 0) >= STALE_S:
        for st in out["stops"]:
            st["current"] = False
    return out


def _trajet_view(snap: dict, plan_file: Any = None) -> dict:
    snap = _d(snap)
    s = _d(snap.get("summary"))
    logq = log_quests(s)
    sp = _d(s.get("plan"))
    pf = plan_file if isinstance(plan_file, dict) else {}
    out: dict = {"stops": [], "source": "", "head": "", "warn": "", "dropped": 0, "minutes": 0}
    steps: list = []
    cursor = 1
    routes = [r for r in _list(pf.get("routes")) if isinstance(r, dict)]
    route = next((r for r in routes if r.get("id") == sp.get("route")), None)
    if route is not None and sp.get("src") == "file":
        steps = [x for x in _list(route.get("steps")) if isinstance(x, dict)]
        if _i(sp.get("made")) and _i(pf.get("made")) and _i(sp.get("made")) != _i(pf.get("made")):
            out["warn"] = "Nouveau trajet calculé : tape /reload pour le charger en jeu."
        else:
            cursor = max(1, _i(sp.get("step"), 1))
            rep = _d(_d(_d(_d(snap.get("char")).get("modules")).get("Planner")).get("repaired"))
            if rep.get("key") == f"{pf.get('made')}:{route.get('id')}" and _list(rep.get("steps")):
                steps = [x for x in _list(rep.get("steps")) if isinstance(x, dict)]
        if sp.get("stale"):
            out["warn"] = "Des quêtes ont changé depuis le calcul : recalcule le trajet."
        made = _f(pf.get("made"))
        now = _f(snap.get("now")) or time.time()
        if made and now - made > 86400 and not out["warn"]:
            out["warn"] = "Trajet calculé il y a plus de 24 h : recalcule-le."
        out["minutes"] = _i(sp.get("left")) or _i(route.get("minutes"))
    names = _map_names(snap.get("db"))
    if steps:
        stops = []
        for st in steps[cursor - 1:]:
            k, q = str(st.get("k") or ""), _i(st.get("q"))
            if logq is not None and q and ((k in ("obj", "turn") and q not in logq) or (k == "pick" and q in logq)):
                out["dropped"] += 1
                continue
            label = _why(st.get("label"))
            if not label and q and logq and q in logq:
                label = f"{'Rendre' if k == 'turn' else 'Faire'} « {logq[q].get('title')} »"
            label = label or STEP_KIND.get(k, "Étape")
            desc = [STEP_KIND[k]] if k in STEP_KIND and STEP_KIND[k].split()[0].lower() not in label.lower() else []
            zone = names.get(_i(st.get("map")))
            if zone:
                desc.append(zone)
            if st.get("who"):
                desc.append(f"pour {st['who']}")
            if st.get("tip"):
                desc.append(_why(st["tip"]))
            stops.append({"n": len(stops) + 1, "title": label, "desc": " · ".join(desc), "current": not stops,
                          "kind": k})
            if len(stops) >= STOPS_MAX:
                break
        out.update(stops=stops, source="plan", head=f"Étape {cursor} sur {len(steps)}"
                   + (f" · {route.get('title')}" if route and route.get("title") else ""))
        return out
    # no route to show: the quest log, best first — never numbered as stops, never « En cours » (an elite quest
    # the route leaves out for a duo was shown « 1. … En cours »)
    fresh = (data_age(snap) or 0) < LIVE_WINDOW_S
    game = sp.get("src") not in (None, "", "none", "file") and not sp.get("stale")
    # (4.0 QA) the stops the game itself shows (« Prochaine étape » card, Trajet page), same labels, same order:
    # summary.plan.next = {{k, q, map, label, min}} (Planner); without it, the quest tracker's order below
    nxt = [x for x in _list(sp.get("next")) if isinstance(x, dict) and _why(x.get("label"))] if game else []
    if nxt:
        prev_g, num = None, 0
        for st in nxt:
            k, q = str(st.get("k") or ""), _i(st.get("q"))
            if logq is not None and q and k in ("obj", "turn") and q not in logq:
                out["dropped"] += 1
                continue
            # (4.0) the in-game stop numbers (Route.Groups): objectives of the same corner share ONE number on the
            # card and the Trajet page, so they do here (``n`` from the addon; an older addon: one number per line)
            g = _i(st.get("n")) or None
            if g is None or g != prev_g or not out["stops"]:
                num += 1
            prev_g = g
            label = _plain(st.get("label"))
            desc = [STEP_KIND[k]] if k in STEP_KIND and STEP_KIND[k].split()[0].lower() not in label.lower() else []
            zone = names.get(_i(st.get("map")))
            if zone:
                desc.append(zone)
            if fresh and _i(st.get("min")) > 0:
                desc.append(f"dans ≈ {duration_text(_i(st['min']) * 60)}")
            out["stops"].append({"n": num, "title": label, "desc": " · ".join(desc),
                                 "current": not out["stops"], "kind": k})
            if len(out["stops"]) >= STOPS_MAX:
                break
        n, tot = _i(sp.get("step")), _i(sp.get("steps"))
        out.update(source="jeu", head=f"Trajet en jeu : étape {n} sur {tot}" if n and tot else "Trajet en jeu",
                   minutes=_i(sp.get("left")))
        return out
    # « En cours » = the quest the in-game route is at (not the tracker's first quest), moved first
    cur_q = _i(sp.get("quest")) if game else 0
    stops = []
    for r in _list(_d(s.get("tracker")).get("route")):
        if not isinstance(r, dict) or not r.get("title"):
            continue
        if logq is not None and _i(r.get("id")) not in logq:
            out["dropped"] += 1
            continue
        tag, why = quest_tag(r)
        # tracker done/total count OBJECTIVES (« 0/1 »): the progress words are the log's (« Croc de loup-garou 6/8 »)
        prog = "" if r.get("ready") else _objective_text(_d((logq or {}).get(_i(r.get("id")))).get("obj"))
        dist = _yd_text(r.get("dist")) if fresh else ""
        desc = " · ".join(x for x in (prog, str(r.get("zone") or ""), dist, why if tag != "Long" else "") if x)
        stops.append({"n": 0, "title": str(r["title"]), "desc": desc, "current": bool(cur_q) and _i(r.get("id")) == cur_q,
                      "kind": "turn" if r.get("ready") else "obj", "tag": tag})
    # no route in game: nothing is « En cours » (the tracker's first quest may be one the route leaves out)
    stops.sort(key=lambda st: not st["current"])           # stable: the current one first, the rest in order
    # (QA-QUEST F5) the quest log is not a route: never numbered as stops (n = 0)
    out["stops"] = stops[:STOPS_MAX]
    if out["stops"]:
        out["source"] = "suivi"
        out["head"] = "Ordre du suivi de quêtes de l'addon (pas de trajet calculé)."
        if sp.get("label") and game:
            out["head"] = f"Trajet en jeu : {_plain(sp['label'])}"
    return out


def _objective_text(obj: Any) -> str:
    """The first unfinished objective of a log quest (« Croc de loup-garou 6/8 »), else the first one."""
    objs = [_why(o.get("text") if isinstance(o, dict) else o) for o in _list(obj) if o]
    objs = [o for o in objs if o]
    for o in objs:
        m = re.search(r"(\d+)\s*/\s*(\d+)", o)
        if m and int(m.group(1)) < int(m.group(2)):
            return o
    return objs[0] if objs else ""


def members_view(snap: dict) -> list[dict]:
    """Member cards of the group: {name, line, badges=[(text, tone)], me}. Squad's snapshot first (names,
    state), else Missions (names), else Team (no names). Only facts of the save."""
    s = _d(_d(snap).get("summary"))
    if not group_ok(s):                                   # yesterday's group, kept in the save: not shown
        return []
    src = [m for m in _list(_d(s.get("group")).get("members")) if isinstance(m, dict)]
    if not src:
        src = [m for m in _list(_d(s.get("missions")).get("members")) if isinstance(m, dict)]
    team = [m for m in _list(_d(s.get("team")).get("members")) if isinstance(m, dict)]
    if not src:
        src = [{**m, "name": f"Membre {i}"} for i, m in enumerate(team, 1)]
    my_name = str(snap.get("char_key") or "").split("-", 1)[0] if isinstance(snap, dict) else ""
    my_zone = str(s.get("zone") or "")
    fresh = (data_age(snap) or 0) < STALE_S           # (4.0 QA) zone, death, bags… of hours ago are not shown
    out = []
    for m in src[:5]:
        name = str(m.get("name") or "?")
        cls = CLASS_FR.get(str(m.get("class") or "").upper(), str(m.get("class") or ""))
        lvl = _i(m.get("level"))
        line = " · ".join(x for x in (f"{cls} {lvl}".strip() if lvl else cls, str(m.get("zone") or "") if fresh else "")
                          if x)
        me = bool(m.get("me")) or (bool(my_name) and name == my_name)
        if not fresh:
            out.append({"name": name, "line": line, "badges": [("Sans TreeAI", "")] if m.get("addon") is False else [],
                        "me": me})
            continue
        badges: list[tuple[str, str]] = []
        if m.get("dead"):
            badges.append(("Mort", "danger"))
        if m.get("afk"):
            badges.append(("Absent", "warning"))
        if m.get("otherZone") or (not me and my_zone and m.get("zone") and str(m["zone"]) != my_zone):
            badges.append(("Autre zone", "warning"))
        elif m.get("far"):
            badges.append(("Loin", "warning"))
        if m.get("addon") is False:
            badges.append(("Sans TreeAI", ""))
        if m.get("bags") is not None and _i(m.get("bags"), -1) == 0:
            badges.append(("Sacs pleins", "warning"))
        du = _f(m.get("durability"))
        if du is not None and 0 <= du < 20:
            badges.append(("À réparer", "warning"))
        if m.get("ready") is False:                       # Group writes ready = true by default: only « pas prêt » is news
            badges.append(("Pas prêt", "warning"))
        out.append({"name": name, "line": line, "badges": badges[:3], "me": me})
    return out


CLASS_FR = {"WARRIOR": "Guerrier", "PALADIN": "Paladin", "HUNTER": "Chasseur", "ROGUE": "Voleur",
            "PRIEST": "Prêtre", "SHAMAN": "Chaman", "MAGE": "Mage", "WARLOCK": "Démoniste", "DRUID": "Druide"}


def dungeon_view(snap: dict) -> dict:
    """« Donjon »: shown only while a dungeon is active (in it, or planned). {active, name, why, boss, next,
    run=[(title, value)], last=[(title, value)]}."""
    s = _d(_d(snap).get("summary"))
    d = _d(s.get("dungeon"))
    age = data_age(snap) or 0
    active = bool(d.get("key")) and age < OLD_S
    out: dict = {"active": active, "stale": age >= STALE_S, "name": str(d.get("name") or d.get("key") or ""),
                 "why": _why(d.get("why")),
                 "boss": str(d.get("boss") or ""), "next": str(d.get("next") or ""), "run": [], "last": []}
    r = _d(d.get("run"))
    if r:
        rows = [("Durée", duration_text(r.get("time")) or "< 1 min")]
        if _i(r.get("kills")):
            rows.append(("Boss tués", str(_i(r["kills"]))))
        rows.append(("Pulls · morts · wipes", f"{_i(r.get('pulls'))} · {_i(r.get('deaths'))} · {_i(r.get('wipes'))}"))
        if _i(r.get("xp")) > 0:
            rows.append(("XP gagnée", fmt_int(r["xp"])))
        if _i(r.get("gold")) > 0:
            rows.append(("Or gagné", money(r["gold"])))
        out["run"] = rows
    last = _d(d.get("last"))
    if last:
        rows = [("Durée", duration_text(last.get("time")) or "< 1 min")]
        if _i(last.get("total")):
            rows.append(("Boss", f"{_i(last.get('kills'))} sur {_i(last['total'])}"))
        rows.append(("Morts", str(_i(last.get("deaths")))))
        if _i(last.get("quests")):
            rows.append(("Quêtes rendues", str(_i(last["quests"]))))
        out["last"] = rows
    return out
