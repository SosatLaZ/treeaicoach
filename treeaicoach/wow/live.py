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
page shows (pure, no Qt, tested with fake files)."""

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
_LEVEL_RE = re.compile(r"(?:atteint le niveau|reached level)\s+(\d+)", re.I)
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
        return {"t": t, "kind": "level", "level": int(lv.group(1))}
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
            elif k == "money":
                self.copper += _i(ev.get("copper"))
                b[1] += _i(ev.get("copper"))
            elif k == "loot":
                name = str(ev.get("item") or "?")
                self.loot[name] = self.loot.get(name, 0) + max(1, _i(ev.get("count"), 1))
            elif k == "level":
                self.levels.append(_i(ev.get("level")))
            elif k == "death":
                self.deaths += 1
            elif k == "zone" and ev.get("zone"):
                self.zone = str(ev["zone"])
        return n

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
            hist = _day_map(e.get("h"))
            low = _day_map(e.get("l"))
            for d, p in low.items():            # daily low when the high is missing
                hist.setdefault(d, p)
            price = _i(e.get("m")) or (hist[max(hist)] if hist else 0)
            if price > 0 and item not in rows:
                rows[item] = {"m": price, "h": hist}
        if rows:
            out[str(realm)] = rows
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


def treeai_series(db: Any, realm_key: str, item: int) -> list[tuple[int, int]]:
    """Our own AH history (``modules.Auction.realms[key].items[id].h``, flat, stride 7) → [(day, med)]."""
    rec = _d(_d(_d(ah_realms(db).get(realm_key)).get("items")).get(item))
    h = _list(rec.get("h"))
    out = []
    for i in range(0, len(h) - 6, 7):
        day, med = _i(h[i]), _i(h[i + 2])
        if med > 0:
            out.append((day, med))
    return sorted(out)[-30:]


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
        from treeaicoach.wow import savedvars  # noqa: PLC0415

        self.db = savedvars.read_db(self.sv.path) if self.sv.sig else {}
        return [LiveEvent("savedvars", "Sauvegarde de l'addon relue" if self.db else "Sauvegarde absente")]

    def _poll_auctionator(self) -> list[LiveEvent]:
        if not self.atr.check():
            return []
        from treeaicoach.wow import savedvars  # noqa: PLC0415

        raw = savedvars.load(self.atr.path).get(AUCTIONATOR_VAR) if self.atr.sig else None
        self.adb = parse_auctionator(raw)
        n = sum(len(v) for v in self.adb.values())
        return [LiveEvent("auctionator", f"{n} prix Auctionator", n)]

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
        return {"now": now, "db": self.db, "char_key": key, "char": ch, "summary": _d(ch.get("summary")),
                "sv_age": self.sv.age(now), "has_sv": bool(self.sv.sig),
                "atr": self.adb, "atr_age": self.atr.age(now),
                "chat": self.chat.to_dict(), "chat_age": self.chat_watch.age(now) if self.chat_tail else None,
                "fights": self._fights(), "combat_age": combat_age}

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._snap)


# ------------------------------------------------------------------------------------- views
def _live(age: Any) -> bool:
    return isinstance(age, (int, float)) and age < LIVE_WINDOW_S


def direct_view(snap: dict) -> dict:
    """« Direct » tab: character, zone, XP/h, gold/h, last fight, deaths. Live sources first."""
    s = _d(snap.get("summary"))
    chat = _d(snap.get("chat"))
    key = str(snap.get("char_key") or "")
    name = key.split("-", 1)[0] if key else ""
    cls = str(s.get("className") or s.get("class") or "")
    title = " · ".join(x for x in (name, f"{cls} niveau {s['level']}" if s.get("level") else cls) if x)
    live_chat = _live(snap.get("chat_age"))
    zone = (chat.get("zone") if live_chat and chat.get("zone") else "") or str(s.get("zone") or "")
    if s.get("subzone") and zone == s.get("zone"):
        zone += f" · {s['subzone']}"
    rows: list[tuple[str, str]] = []
    pred = _d(_d(s.get("predict")).get("rates"))
    boost, eco = _d(s.get("boost")), _d(s.get("economy"))
    xph = chat.get("xp_h") if live_chat else None
    gph = chat.get("gold_h") if live_chat else None
    xph_src = "en direct" if xph else ""
    if not xph:
        xph = _i(pred.get("xpPerHour")) or _i(boost.get("xpPerHour")) or None
        xph_src = "au /reload" if xph else ""
    if not gph:
        gph = _i(pred.get("goldPerHour")) or _i(eco.get("goldPerHour")) or None
    if xph:
        txt = f"{xph:,} XP/h".replace(",", " ")
        if s.get("xpMax"):
            left = max(0, _i(s.get("xpMax")) - _i(s.get("xp")) - (chat.get("xp", 0) if live_chat else 0))
            txt += f" · niveau dans ~{duration_text(left * 3600 / max(1, xph))}" if left else ""
        rows.append(("XP par heure", f"{txt} ({xph_src})"))
    if gph:
        rows.append(("Or par heure", f"{money(gph)}/h" + (" (en direct)" if live_chat and chat.get("gold_h") else "")))
    if s.get("xpMax"):
        pct = int(100 * _i(s.get("xp")) / max(1, _i(s.get("xpMax"))))
        rows.append(("Expérience", f"{pct} %" + (" · reposé" if s.get("rested") else "")))
    if s.get("money") is not None:
        rows.append(("Or", money(s.get("money"))))
    fights = _d(snap.get("fights"))
    fight_s = _d(s.get("fight"))
    last = ""
    if fights.get("last_text"):
        last = str(fights["last_text"])
        mis = _list(_d(fights.get("last")).get("mistakes"))
        if mis:
            last += " " + str(mis[0])
    elif fight_s.get("lastDeath"):
        ld = _d(fight_s["lastDeath"])
        last = (f"Dernière mort : {_i(ld.get('attackers'))} attaquant(s), {_i(ld.get('total'))} dégâts. "
                f"{ld.get('advice') or ''}").strip()
    rows.append(("Dernier combat", last or "Aucun combat lu (Forever n'écrit pas toujours le journal de combat)."))
    deaths = max(_i(fights.get("deaths")), _i(chat.get("deaths")) if live_chat else 0, _i(fight_s.get("deaths")))
    rows.append(("Morts", str(deaths) if deaths else "Aucune"))
    xp_series = chat.get("xp_series") if live_chat else []
    gold_series = chat.get("gold_series") if live_chat else []
    sources = []
    if live_chat:
        sources.append("journal de discussion en direct")
    if _live(snap.get("combat_age")):
        sources.append("journal de combat en direct")
    return {"title": title or "Personnage inconnu", "zone": zone, "rows": rows, "xp_series": xp_series or [],
            "gold_series": gold_series or [], "live": bool(sources),
            "live_text": ("Sources : " + ", ".join(sources) + ".") if sources else
            "Pas de journal en direct : tape /chatlog en jeu pour l'XP et l'or en temps réel.",
            "empty": not s}


def _grey_gap(level: int) -> int:
    """Classic-like grey threshold (quest level ≤ player − gap is grey)."""
    if level <= 5:
        return 99
    if level < 40:
        return 5 + level // 10
    return 1 + level // 5


def abandon_advice(s: dict, group_size: int = 1) -> list[dict]:
    """Quests worth dropping, each with WHY (« grise », « élite seul », « trop longue »…). The addon's own
    list (``summary.abandon``) wins when present; otherwise derived from ``summary.quests`` + tracker."""
    own = _list(s.get("abandon"))
    if own:
        out = []
        for a in own[:10]:
            if isinstance(a, dict):
                out.append({"title": str(a.get("title") or a.get("q") or "?"),
                            "why": str(a.get("why") or a.get("reason") or "elle ne vaut plus le coup")})
            elif a:
                out.append({"title": str(a), "why": "conseillé par l'addon"})
        return out
    lvl = _i(s.get("level"))
    qs = [q for q in _list(_d(s.get("quests")).get("list")) if isinstance(q, dict)]
    route = {_i(r.get("id")): r for r in _list(_d(s.get("tracker")).get("route")) if isinstance(r, dict)}
    xpms = sorted(_i(r.get("xpm")) for r in route.values() if _i(r.get("xpm")) > 0)  # computation only, never shown
    med = xpms[len(xpms) // 2] if xpms else 0
    gsize = max(1, _i(s.get("groupSize"), group_size) or group_size)
    out = []
    for q in qs:
        if q.get("complete"):
            continue
        ql, title = _i(q.get("level")), str(q.get("title") or "?")
        why = ""
        if q.get("grey") or (lvl and ql and ql <= lvl - _grey_gap(lvl)):
            why = "trop facile maintenant, presque pas d'XP"
        elif q.get("hard") and gsize < 3:
            why = "trop dure seul : tu risques de mourir pour pas grand-chose"
        elif lvl and ql >= lvl + 4:
            why = "trop dure pour l'instant : reviens plus tard"
        else:
            r = route.get(_i(q.get("id")))
            if r and med and 0 < _i(r.get("xpm")) < med * 0.25:
                why = "beaucoup trop longue pour ce qu'elle rapporte"
        if why:
            out.append({"title": title, "why": why})
    return out[:10]


#: The 5 status words of a quest (same as the addon, Modules/Plain.lua) — never XP/min, score or %.
QUEST_TAGS = ("Prête à rendre", "À retirer", "À plusieurs", "Rapide", "Long")
#: Words that must never reach the main quest UI (robustness check, see tests).
JARGON = ("xp/min", "xp/h", "/min", "score", "λ", "/100", "coût", "rendement", "coefficient")


def is_plain(text: str) -> bool:
    """True when a main-UI quest line speaks plain French: no XP/min, score, λ, cost… and « % » only for a
    progress (« niveau 27 : 62 % »)."""
    low = str(text or "").lower()
    if any(w in low for w in JARGON):
        return False
    if "%" in low and not any(k in low for k in ("niv", "progress", "fini")):
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
    """« Groupe » tab: the missions board of the 4 (summary.missions)."""
    s = _d(snap.get("summary"))
    m = _d(s.get("missions"))
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
    return {"members": members, "board": board, "next": str(m.get("next") or ""),
            "assign": [str(x) for x in _list(m.get("assign"))][:6], "claims": claims[:6],
            "travel": [str(x) for x in _list(m.get("travel"))][:5],
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
    faction = our_key.split("-")[1] if our_key.count("-") >= 1 else ""
    atr_key = pick_realm(adb.keys(), realm_of(key), faction, {k: len(v) for k, v in adb.items()})
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
                      "atr": atr_price, **n,
                      "ours": treeai_series(db, our_key, item) if item else [],
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
    realized = {"today": _i(r.get("today")), "week": _i(r.get("week")), "total": _i(r.get("total", rp["total"])),
                "top": [(str(t.get("item")), _i(t.get("profit"))) for t in _list(r.get("top")) if isinstance(t, dict)][:5],
                "series": [float(v) for _k, v in days]}
    info = []
    if a.get("scanAge") is not None:
        info.append(f"dernier scan {age_text(_i(a.get('scanAge')))} avant le /reload")
    info.append("HV neutre : commission 15 %" if neutral else "commission 5 %")
    if adb:
        info.append(f"Auctionator : {sum(len(v) for v in adb.values())} prix ({atr_key or '?'})")
    gold = _d(a.get("gold"))
    budget = ""
    if gold:
        budget = (f"Or {money(gold.get('total'))} · réserve {money(gold.get('reserve'))} · "
                  f"immobilisé {money(gold.get('invested'))}" + (f" · budget restant {money(a['budgetLeft'])}"
                                                                 if a.get("budgetLeft") is not None else ""))
    return {"deals": deals, "mine": mine, "mine_known": "owned" in a,
            "recent": [(str(e.get("item") or "?"), _i(e.get("money")) - _i(e.get("cut")), str(e.get("day") or ""))
                       for e in recent],
            "realized": realized, "info": " · ".join(info), "budget": budget,
            "thin": [str(x) for x in _list(a.get("thin"))][:5], "empty": not a and not adb}


def session_view(snap: dict) -> dict:
    """« Session » tab: recap of the play session (chat log live + missions session) and goals."""
    s = _d(snap.get("summary"))
    chat = _d(snap.get("chat"))
    ses = _d(_d(s.get("missions")).get("session"))
    rows: list[tuple[str, str]] = []
    if chat.get("events"):
        rows.append(("Durée (journal en direct)", duration_text(chat.get("duration")) or "< 1 min"))
        rows.append(("XP gagnée", f"{_i(chat.get('xp')):,}".replace(",", " ") +
                     (f" · {_i(chat.get('kills'))} monstres" if chat.get("kills") else "")))
        if chat.get("copper"):
            rows.append(("Or ramassé", money(chat.get("copper"))))
        if chat.get("levels"):
            rows.append(("Niveaux", ", ".join(str(x) for x in chat["levels"])))
        loot = ", ".join(f"{n} x{c}" if c > 1 else str(n) for n, c in _list(chat.get("loot"))[:5])
        if loot:
            rows.append(("Butin", loot))
    if ses:
        rows.append(("Session du groupe", f"{duration_text(ses.get('duration')) or '?'} · {_i(ses.get('turned'))} quêtes "
                                          f"rendues · {_i(ses.get('missions'))} missions · {_i(ses.get('deaths'))} morts"))
        for lv in _list(ses.get("levels"))[:4]:
            if isinstance(lv, dict) and _i(lv.get("to")) > _i(lv.get("from")):
                rows.append((str(lv.get("name")), f"niveau {_i(lv.get('from'))} → {_i(lv.get('to'))}"))
    eco = _d(s.get("economy"))
    if eco.get("lootValue"):
        rows.append(("Valeur du butin", money(eco.get("lootValue"))))
    pred = _d(s.get("predict"))
    if pred.get("sessionMin") and not chat.get("events"):
        rows.append(("Durée (au /reload)", duration_text(_i(pred["sessionMin"]) * 60)))
    goals = []
    g = _d(ses.get("goal"))
    if g:
        txt = str(g.get("text") or g.get("kind") or "Objectif")
        eta = f" · ~{duration_text(g.get('eta'))}" if _i(g.get("eta")) > 0 else ""
        goals.append({"text": txt + (" — atteint !" if g.get("done") else eta), "pct": max(0, min(100, _i(g.get("pct"))))})
    if s.get("xpMax"):
        pct = int(100 * _i(s.get("xp")) / max(1, _i(s.get("xpMax"))))
        boost = _d(s.get("boost"))
        eta = f" · ~{duration_text(boost.get('secondsToLevel'))}" if _i(boost.get("secondsToLevel")) else ""
        goals.append({"text": f"Niveau {_i(s.get('level')) + 1}{eta}", "pct": pct})
    q = _d(s.get("quests"))
    if q.get("questsToLevel"):
        goals.append({"text": f"~{_i(q['questsToLevel'])} quêtes pour le niveau suivant", "pct": None})
    board = [(str(e.get("name")), _i(e.get("xph"))) for e in _list(ses.get("leaderboard")) if isinstance(e, dict)]
    events = [str(x) for x in _list(ses.get("events"))][:6]
    return {"rows": rows, "goals": goals, "leaderboard": board, "events": events,
            "empty": not rows and not goals}
