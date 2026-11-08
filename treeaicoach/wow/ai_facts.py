"""Deterministic facts for the WoW « Hey coach » answers (:mod:`.ai_context` turns them into the prompt).

Every number comes from the game's own files — the addon's SavedVariables ``TreeAIDB`` (written at /reload or
logout) and the live ``Logs/WoWChatLog.txt`` — or from a documented formula applied to them (Classic XP curve
and quest XP decay, the route planner's travel and objective models). A value we do not have is NOT guessed:
the section lists it under ``manque`` and the coach says « je n'ai pas encore la donnée ». Knowledge that comes
from Classic (zone level bands, dungeon max levels, tameable beasts) is labelled « d'après Classic »: Forever changed
parts of the game.

Each builder returns a :class:`Section` (French keys and texts, compact) with the age of its data; a volatile
section (group state, AH prices, XP/h at the /reload…) older than :data:`STALE_S` is dropped by the assembler,
the others are kept with an ``age`` label. Player names never leave this module: they are replaced by
« Classe niveau » labels (« toi » for the player). Pure functions, never raise (``log.debug`` on errors)."""

from __future__ import annotations

import logging
import math
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

STALE_S = 15 * 60          # older than this: volatile data dropped, the rest labelled with its age
LIVE_S = 10 * 60           # a chat log written in the last 10 min is « en direct »
MEASURE_MIN_S = 10 * 60    # an XP/h needs at least 10 min of measure
AH_MAX_AGE_S = 24 * 3600   # AH deals from an older scan are never offered
EPOCH_MIN = 1.5e9          # a plausible time() (2017+)

CLASS_FR = {"WARRIOR": "Guerrier", "PALADIN": "Paladin", "HUNTER": "Chasseur", "ROGUE": "Voleur",
            "PRIEST": "Prêtre", "SHAMAN": "Chaman", "MAGE": "Mage", "WARLOCK": "Démoniste", "DRUID": "Druide"}
HAPPINESS_FR = {1: "mécontent", 2: "content", 3: "heureux"}
VERDICT_FR = {"ok": "OK", "galere": "on va galérer", "long": "trop long", "impossible": "pas faisable",
              "zone": "changez de zone"}
ROLE_FR = {"TANK": "tank", "HEALER": "soigneur", "DAMAGER": "dégâts"}
NUM_WORDS = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8,
             "neuf": 9, "dix": 10}


# ------------------------------------------------------------------ small helpers
def _d(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _l(v: Any) -> list:
    """A Lua array parsed as a list or as {1: …, 2: …} → list (in order)."""
    if isinstance(v, list):
        return [x for x in v if x is not None]
    if isinstance(v, dict):
        keys = sorted(k for k in v if isinstance(k, int))
        return [v[k] for k in keys]
    return []


def _n(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _i(v: Any, default: int = 0) -> int:
    f = _n(v)
    return int(f) if f is not None else default


def short(text: Any, limit: int = 120) -> str:
    s = " ".join(str(text if text is not None else "").split())
    return s if len(s) <= limit else s[:limit - 1].rstrip(" ,;:") + "…"


def fold(text: Any) -> str:
    """Lower case, no accents, typographic apostrophes → ' (for matching French questions)."""
    s = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    return s.lower().replace("’", "'")


def num_fr(n: Any) -> str:
    """21300 → « 21 300 » (French thousands)."""
    return f"{int(round(_n(n) or 0)):,}".replace(",", " ")


def dur_fr(sec: Any) -> str:
    """Seconds → « 51 min », « 1 h 05 », « moins d'une minute » (spoken French)."""
    s = _n(sec)
    if s is None or s < 0:
        return ""
    if s < 60:
        return "moins d'une minute"
    m = int(round(s / 60.0))
    if m < 60:
        return f"{m} min"
    h, mm = divmod(m, 60)
    return f"{h} h" if mm == 0 else f"{h} h {mm:02d}"


def age_fr(sec: Any) -> str:
    s = _n(sec)
    if s is None:
        return ""
    if s < 90:
        return "à l'instant"
    if s < 3600:
        return f"il y a {int(s // 60)} min"
    if s < 86400:
        return f"il y a {int(s // 3600)} h"
    return f"il y a {int(s // 86400)} j"


def money_fr(copper: Any) -> str | None:
    """Copper → « 12 po 34 pa » (copper dropped from 10 po)."""
    c = _n(copper)
    if c is None or c < 0:
        return None
    c = int(c)
    g, s, cp = c // 10000, (c // 100) % 100, c % 100
    parts = [f"{g} po"] if g else []
    if s:
        parts.append(f"{s} pa")
    if (cp and g < 10) or not parts:
        parts.append(f"{cp} pc")
    return " ".join(parts)


def class_fr(v: Any) -> str:
    s = str(v or "").strip()
    return CLASS_FR.get(s.upper(), s) or "?"


def _optional(name: str) -> Any:
    try:
        from treeaicoach.wow import optional  # noqa: PLC0415

        return optional(name)
    except Exception:
        return None


# ------------------------------------------------------------------ the snapshot
@dataclass
class Section:
    key: str
    data: Any
    age: float | None = None      # seconds since the data was true (None: live / unknown)
    volatile: bool = False        # dropped (not labelled) when older than STALE_S


@dataclass
class Snap:
    """Everything the facts are built from (one question)."""
    now: float
    db: dict
    key: str = ""                 # "Name-Realm" of the player (never sent)
    char: dict = field(default_factory=dict)
    s: dict = field(default_factory=dict)          # chars[key].summary (legacy flat fields merged under it)
    age: float | None = None      # seconds since the summary was written in game
    chat: dict = field(default_factory=dict)       # live.ChatSession.to_dict()
    chat_age: float | None = None
    events: list = field(default_factory=list)     # parsed chat events {t, kind, xp, level, zone…}
    plan: dict | None = None      # TreeAI_Plan written by the app
    facts: list = field(default_factory=list)      # combat-log facts (coach)
    labels: dict = field(default_factory=dict)     # player name → « Prêtre 25 » / « toi »

    @property
    def export(self) -> dict:
        return _d(self.db.get("export"))

    @property
    def live(self) -> bool:
        return self.chat_age is not None and self.chat_age < LIVE_S and bool(self.chat.get("events"))

    @property
    def stale(self) -> bool:
        return self.age is None or self.age > STALE_S

    def sub(self, key: str) -> dict:
        return _d(self.s.get(key))


_SKIP_CHAR_KEYS = {"summary", "modules", "suggestions", "panel", "layout", "movers", "dismissed", "learn",
                   "recentAlerts"}


def _updated(c: dict) -> float:
    return _n(_d(c.get("summary")).get("updated")) or _n(c.get("updated")) or 0.0


def pick_char(db: Any) -> tuple[str, dict, dict]:
    """(key, char entry, merged summary) of the most recently saved character. Accepts the real layout
    (``chars[key].summary``), a ``char`` entry, or a flat table (tests / older files)."""
    db = _d(db)
    chars = db.get("chars")
    if isinstance(chars, list):
        chars = {str(i): c for i, c in enumerate(chars)}
    best: tuple[str, dict] = ("", {})
    if isinstance(chars, dict):
        cands = [(str(k), c) for k, c in chars.items() if isinstance(c, dict)]
        if cands:
            best = max(cands, key=lambda kc: _updated(kc[1]))
    elif isinstance(db.get("char"), dict):
        best = ("", db["char"])
    elif any(k in db for k in ("class", "level", "zone")):
        best = ("", db)
    key, c = best
    if not c:
        return "", {}, {}
    flat = {k: v for k, v in c.items() if k not in _SKIP_CHAR_KEYS}
    merged = {**flat, **_d(c.get("summary"))}
    return key, c, merged


def make_snap(db: Any, now: float | None = None, chat: Any = None, chat_age: Any = None,
              events: Iterable[Any] | None = None, plan: Any = None, facts: Iterable[Any] | None = None,
              sv_age: Any = None) -> Snap:
    t = time.time() if now is None else float(now)
    key, c, s = pick_char(db)
    upd = _n(s.get("updated"))
    age = None
    if upd and upd > EPOCH_MIN and upd <= t + 300:
        age = max(0.0, t - upd)
    elif _n(sv_age) is not None:
        age = max(0.0, float(_n(sv_age) or 0.0))
    snap = Snap(now=t, db=_d(db), key=key, char=c, s=s, age=age, chat=_d(chat), chat_age=_n(chat_age),
                events=[e for e in (events or []) if isinstance(e, dict)], plan=plan if isinstance(plan, dict) else None,
                facts=[short(f, 140) for f in (facts or []) if f])
    snap.labels = name_labels(snap)
    return snap


# ------------------------------------------------------------------ names → labels (never send names)
def _label(cls: Any, level: Any) -> str:
    lv = _i(level)
    return " ".join(x for x in (class_fr(cls), str(lv) if lv else "") if x)


def name_labels(snap: Snap) -> dict[str, str]:
    """Every player / guild / realm name found in the data → what replaces it in the prompt."""
    out: dict[str, str] = {}
    s = snap.s

    def add(name: Any, label: str) -> None:
        n = str(name or "").strip()
        if len(n) >= 2 and n not in out:
            out[n] = label

    if snap.key:
        me, _, realm = snap.key.partition("-")
        add(snap.key, "toi")
        add(me, "toi")
        if realm:
            add(realm, "ton royaume")
    lv = s.get("leveling")
    if isinstance(lv, dict):
        add(lv.get("name"), "toi")
        if lv.get("realm"):
            add(lv.get("realm"), "ton royaume")
    if s.get("guild"):
        add(s.get("guild"), "ta guilde")
    for m in _l(snap.sub("group").get("members")):
        if isinstance(m, dict) and m.get("name"):
            add(m["name"], "toi" if m.get("me") else _label(m.get("class"), m.get("level")))
    mi = snap.sub("missions")
    for m in _l(mi.get("members")):
        if isinstance(m, dict) and m.get("name"):
            add(m["name"], _label(m.get("class"), m.get("level")))
    for m in _l(snap.export.get("squad")):
        if isinstance(m, dict) and m.get("key"):
            nm = str(m["key"]).partition("-")[0]
            add(m["key"], _label(m.get("class"), m.get("level")))
            add(nm, _label(m.get("class"), m.get("level")))
    peers = _d(_d(snap.db.get("group")).get("peers"))
    for k, p in peers.items():
        if isinstance(p, dict):
            add(str(k).partition("-")[0], _label(p.get("class") or p.get("c"), p.get("level") or p.get("l")))
    ses = _d(mi.get("session"))
    for e in _l(ses.get("leaderboard")) + _l(ses.get("levels")):
        if isinstance(e, dict) and e.get("name"):
            add(e["name"], "un membre du groupe")
    for e in _l(mi.get("claims")):
        if isinstance(e, dict) and e.get("by"):
            add(e["by"], "un membre du groupe")
    meter = _d(snap.sub("dungeon").get("meter"))
    if meter.get("top"):
        add(meter["top"], "un membre du groupe")
    return out


def anonymize(text: str, labels: dict[str, str]) -> str:
    """Replace whole-word names (longest first). Case-sensitive: the game writes names capitalised, so a friend
    called « Ours » leaves « 4 ours enragés » alone (a capitalised « Ours » is replaced: privacy first)."""
    if not labels or not text:
        return text
    out = text
    for name in sorted(labels, key=len, reverse=True):
        out = re.sub(r"(?<![\w])" + re.escape(name) + r"(?![\w])", labels[name], out)
    return out


def anonymize_tree(v: Any, labels: dict[str, str]) -> Any:
    if isinstance(v, str):
        return anonymize(v, labels)
    if isinstance(v, list):
        return [anonymize_tree(x, labels) for x in v]
    if isinstance(v, dict):
        return {k: anonymize_tree(x, labels) for k, x in v.items()}
    return v


# ------------------------------------------------------------------ live progress (chat log since the /reload)
def live_progress(snap: Snap) -> dict:
    """XP and levels gained since the summary was written (chat log, when live): {level, xp_since, leveled}."""
    out = {"level": _i(snap.s.get("level")), "xp_since": 0, "leveled": False}
    t0 = _n(snap.s.get("updated"))
    if not snap.live or not t0:
        return out
    for e in sorted(snap.events, key=lambda e: _n(e.get("t")) or 0):
        t = _n(e.get("t"))
        if t is None or t <= t0:
            continue
        if e.get("kind") == "level" and _i(e.get("level")) > out["level"]:
            out.update(level=_i(e.get("level")), xp_since=0, leveled=True)
        elif e.get("kind") == "xp":
            out["xp_since"] += _i(e.get("xp"))
    return out


def xp_needed(level: int) -> int:
    try:
        from treeaicoach.wow.planner import models  # noqa: PLC0415

        return int(models.xp_needed(int(level)))
    except Exception:
        return 0


# ------------------------------------------------------------------ identity
def identity(snap: Snap) -> Section:
    s = snap.s
    d: dict[str, Any] = {}
    cls = s.get("className") or s.get("classFR") or s.get("class")
    if cls:
        d["classe"] = class_fr(cls)
    prog = live_progress(snap)
    if prog["level"]:
        d["niveau"] = prog["level"]
    fac = _d(s.get("leveling")).get("faction") or _d(snap.export.get("player")).get("faction") or s.get("faction")
    if fac in ("Horde", "Alliance"):
        d["faction"] = fac
    xp, xpmax = _n(s.get("xp")), _n(s.get("xpMax"))
    if prog["leveled"]:
        need = xp_needed(prog["level"])
        if need:
            d["xp"] = f"{min(99, round(100 * prog['xp_since'] / need))} % du niveau (niveau pris en direct)"
    elif xp is not None and xpmax:
        d["xp"] = f"{min(99, round(100 * (xp + prog['xp_since']) / xpmax))} % du niveau"
    rest = _n(s.get("rested"))
    if rest and xpmax:
        d["repos"] = f"{round(100 * rest / xpmax)} % d'un niveau en XP reposée"
    zone = ""
    if snap.live and snap.chat.get("zone"):
        zone = str(snap.chat["zone"]) + " (en direct)"
    elif s.get("zone"):
        zone = str(s.get("zone")) + (f" · {s['subzone']}" if s.get("subzone") and s.get("subzone") != s.get("zone") else "")
    if zone:
        d["zone"] = short(zone, 70)
    gold = money_fr(s.get("money"))
    if gold:
        d["or"] = gold
    free, total = _n(s.get("bagsFree")), _n(s.get("bagsTotal"))
    b = _d(s.get("bags"))
    free = free if free is not None else _n(b.get("free"))
    total = total if total is not None else _n(b.get("total"))
    if free is not None:
        d["sacs"] = f"{int(free)} places libres" + (f" sur {int(total)}" if total else "")
    inv, eco = snap.sub("inventory"), snap.sub("economy")
    dmin = _n(inv.get("durMin"))
    if dmin is not None and dmin < 35:
        cost = money_fr(inv.get("repairCost") or eco.get("repairCost"))
        d["reparation"] = f"un objet à {int(dmin)} % de durabilité" + (f", réparer ≈ {cost}" if cost else "")
    gs = _i(s.get("groupSize"))
    if gs > 1:
        d["groupe"] = f"{gs} joueurs"
    return Section("joueur", d, snap.age)   # gold / bags are from the /reload even when zone / XP are live


# ------------------------------------------------------------------ XP/h and level ETA
def _xph_sources(snap: Snap) -> list[dict]:
    """Every XP/h we can trust, best first: {xph, src, conf, minutes?, age?}."""
    out: list[dict] = []
    ch = snap.chat
    dur = _n(ch.get("duration")) or 0.0
    if snap.live and dur >= MEASURE_MIN_S and _i(ch.get("xp")) > 0:
        m = int(dur // 60)
        out.append({"xph": _i(ch.get("xp")) * 3600.0 / dur, "src": f"mesurée en direct sur {m} min",
                    "conf": "bonne" if m >= 30 else "moyenne", "age": None})
    st = _d(snap.sub("stats").get("xph"))
    v = _n(st.get("recent")) or _n(st.get("all"))
    if v and v > 0:
        out.append({"xph": v, "src": "mesurée par l'addon sur ta dernière heure de jeu", "conf": "moyenne",
                    "age": snap.age, "volatile": True})
    learn = _d(snap.char.get("learn")).get("xph")
    if isinstance(learn, dict) and _n(learn.get("n")) and _n(learn.get("s")):
        n = _i(learn.get("n"))
        mean = (_n(learn.get("s")) or 0.0) / max(1, n)
        if mean > 0:
            out.append({"xph": mean, "src": f"moyenne de tes {n} dernières sessions",
                        "conf": "bonne" if n >= 5 else "moyenne" if n >= 2 else "faible", "age": None})
    p = snap.sub("plan")
    if _n(p.get("xph")) and _i(p.get("minutes")) >= 10:
        out.append({"xph": _n(p.get("xph")), "src": "prévue par le trajet (pas mesurée)", "conf": "faible",
                    "age": snap.age, "volatile": True})
    return out


def ready_quests_xp(snap: Snap) -> tuple[int, int]:
    """(ready quests, XP they give if every base XP is known else 0)."""
    level = live_progress(snap)["level"] or _i(snap.s.get("level"))
    qs = [q for q in _l(snap.export.get("quests")) if isinstance(q, dict) and q.get("st", "log") == "log"]
    ready = [q for q in qs if _i(q.get("done")) == 1]
    if not ready:
        n = _i(snap.sub("quests").get("complete"))
        return n, 0
    try:
        from treeaicoach.wow.planner import models  # noqa: PLC0415

        if all(_i(q.get("xp")) > 0 for q in ready):
            return len(ready), sum(models.quest_xp(_i(q.get("xp")), level, _i(q.get("lv")) or level) for q in ready)
    except Exception:
        log.debug("ready quests xp failed", exc_info=True)
    return len(ready), 0


def xp_rate(snap: Snap) -> Section:
    """XP/h (with where it comes from and how sure it is) and the time to the next level."""
    d: dict[str, Any] = {}
    manque: list[str] = []
    prog = live_progress(snap)
    level = prog["level"]
    xp, xpmax = _n(snap.s.get("xp")), _n(snap.s.get("xpMax"))
    left = None
    if prog["leveled"]:
        need = xp_needed(level)
        left = max(0, need - prog["xp_since"]) if need else None
    elif xp is not None and xpmax:
        left = max(0.0, xpmax - xp - prog["xp_since"])
    srcs = [x for x in _xph_sources(snap) if not (x.get("volatile") and (x.get("age") or 0) > STALE_S)]
    if level and level >= 60:
        d["niveau_max"] = "tu es niveau 60 : plus d'XP à gagner"
        return Section("xp", d)
    if left is not None:
        d["xp_restante"] = num_fr(left)
    if srcs:
        best = srcs[0]
        xph = int(round(best["xph"] / 100.0) * 100)
        d["xp_par_heure"] = num_fr(xph)
        d["source"] = best["src"]
        d["confiance"] = best["conf"]
        if left is not None and xph > 0 and level:
            d[f"niveau_{level + 1}_dans"] = "≈ " + dur_fr(left * 3600.0 / xph)
        if len(srcs) > 1:
            o = srcs[1]
            d["autre_mesure"] = f"{num_fr(round(o['xph'] / 100.0) * 100)} XP/h ({o['src']})"
    else:
        manque.append("ton XP par heure n'est pas encore mesurée, joue 10 minutes avec /chatlog actif")
    n_ready, xp_ready = ready_quests_xp(snap)
    if n_ready:
        d["quetes_pretes"] = f"{n_ready} à rendre" + (f", ≈ {num_fr(xp_ready)} XP" if xp_ready else "")
        if xp_ready and left is not None and xp_ready >= left:
            d["astuce"] = "rendre tes quêtes prêtes suffit pour monter de niveau"
    if snap.live and _i(snap.chat.get("xp")):
        d["session_en_direct"] = (f"{num_fr(snap.chat.get('xp'))} XP en {dur_fr(snap.chat.get('duration')) or '1 min'}"
                                  + (f", {_i(snap.chat.get('kills'))} monstres" if snap.chat.get("kills") else ""))
    if manque:
        d["manque"] = manque
    return Section("xp", d, None if snap.live else snap.age)


# ------------------------------------------------------------------ quests
def quests(snap: Snap, max_items: int = 6) -> Section:
    qs = snap.sub("quests")
    tr = snap.sub("tracker")
    d: dict[str, Any] = {}
    lst = [q for q in _l(qs.get("list")) if isinstance(q, dict) and q.get("title")]
    if not lst:
        lst = [q for q in _l(snap.s.get("quests")) if isinstance(q, (dict, str))]   # legacy flat
    route = {_i(r.get("id")): r for r in _l(tr.get("route")) if isinstance(r, dict)}
    order = [_i(r.get("id")) for r in _l(tr.get("route")) if isinstance(r, dict)]
    count = _i(qs.get("count")) or len(lst)
    mx = _i(_d(snap.export.get("player")).get("logMax")) or 0
    if count:
        d["journal"] = f"{count}/{mx}" if mx else str(count)
    ready, doing = [], []

    def objs(q: dict) -> str:
        o = q.get("obj") if q.get("obj") is not None else q.get("objectives")
        out = []
        for x in _l(o) if not isinstance(o, str) else [o]:
            if isinstance(x, dict):
                if x.get("done") in (True, 1):
                    continue
                x = x.get("text") or x.get("t")
            if x:
                out.append(short(x, 50))
        return "; ".join(out[:2])

    def rank(q: Any) -> int:
        qid = _i(q.get("id")) if isinstance(q, dict) else 0
        return order.index(qid) if qid in order else 999

    for q in sorted(lst, key=rank):
        if isinstance(q, str):
            doing.append(short(q, 80))
            continue
        lvl = _i(q.get("level"))
        title = short(q.get("title"), 45) + (f" [{lvl}]" if lvl else "")
        r = route.get(_i(q.get("id")), {})
        if q.get("complete") in (True, 1) or r.get("ready") or q.get("done") is True:
            ready.append(title)
            continue
        t = title
        o = objs(q)
        if o:
            t += f" : {o}"
        if _i(r.get("sec")):
            t += f" (≈ {dur_fr(r.get('sec'))})"
        if q.get("grey"):
            t += " — grise"
        doing.append(short(t, 110))
    if ready:
        d["pretes_a_rendre"] = ready[:4]
    if doing:
        d["en_cours"] = doing[:max_items]
    ab = [a for a in _l(snap.sub("abandon").get("list")) if isinstance(a, dict) and a.get("title")]
    if ab:
        d["a_abandonner"] = [f"{short(a.get('title'), 40)} : {short(a.get('why') or 'ne vaut plus le coup', 60)}"
                             for a in ab[:2]]
    if _i(tr.get("totalMin")):
        d["tout_finir"] = "≈ " + dur_fr(_i(tr.get("totalMin")) * 60)
    if mx and count >= mx:
        d["alerte"] = "journal plein : rends ou abandonne une quête avant d'en prendre"
    return Section("quetes", d, snap.age)


# ------------------------------------------------------------------ the route / next step
def zone_bands() -> dict[int, tuple[int, int, str]]:
    """uiMapID → (min, max, name) from the addon's Leveling.lua (d'après Classic). {} when absent."""
    return _lua_tables()["zones"]


def hubs() -> list[tuple[int, int, str | None, str]]:
    return _lua_tables()["hubs"]


_LUA_CACHE: dict[str, Any] = {}
_LUA_LOCK = threading.Lock()


def _addon_file(*parts: str) -> Path | None:
    inst = _optional("install")
    try:
        src = inst.addon_source_dir() if inst is not None else None
    except Exception:
        src = None
    if src is None:
        src = Path(__file__).resolve().parents[2] / "wow_addon" / "TreeAI"
    p = Path(src).joinpath(*parts)
    return p if p.is_file() else None


def _lua_tables() -> dict[str, Any]:
    with _LUA_LOCK:
        if _LUA_CACHE:
            return _LUA_CACHE
        zones: dict[int, tuple[int, int, str]] = {}
        hub: list[tuple[int, int, str | None, str]] = []
        dungeons: list[dict] = []
        try:
            p = _addon_file("Modules", "Leveling.lua")
            txt = p.read_text(encoding="utf-8") if p else ""
            for mid, lo, hi, name in re.findall(r'\[(\d+)\]\s*=\s*\{\s*(\d+),\s*(\d+),\s*"([^"]+)"\s*\}', txt):
                zones[int(mid)] = (int(lo), int(hi), name)
            for lo, hi, fac, text in re.findall(r'\{\s*(\d+),\s*(\d+),\s*(nil|"Horde"|"Alliance"),\s*"([^"]+)"\s*\}', txt):
                hub.append((int(lo), int(hi), None if fac == "nil" else fac.strip('"'), text))
        except Exception:
            log.debug("Leveling.lua tables unreadable", exc_info=True)
        try:
            p = _addon_file("Data", "Dungeons.lua")
            txt = p.read_text(encoding="utf-8") if p else ""
            for m in re.finditer(r'\{\s*key\s*=\s*"(\w+)",\s*n\s*=\s*"([^"]+)",([^\n]*?)min\s*=\s*(\d+),\s*max\s*=\s*(\d+)([^\n]*)', txt):
                key, name, mid, lo, hi, rest = m.groups()
                kw = re.findall(r'"([^"]+)"', rest.split("kw", 1)[1]) if "kw" in rest else []
                dungeons.append({"key": key, "n": name, "min": int(lo), "max": int(hi),
                                 "new": "new = true" in (mid + rest), "kw": kw})
        except Exception:
            log.debug("Dungeons.lua unreadable", exc_info=True)
        _LUA_CACHE.update(zones=zones, hubs=hub, dungeons=dungeons)
        return _LUA_CACHE


def zones_for_level(level: int, faction: str | None = None) -> str | None:
    for lo, hi, fac, text in hubs():
        if lo <= level < hi and (fac is None or fac == faction):
            return text
    return None


def route_plan(snap: Snap) -> Section:
    """What TreeAI shows in game as the next step (todo / in-game plan), the app's plan, zone advice."""
    d: dict[str, Any] = {}
    now = _d(snap.sub("todo").get("now"))
    if now.get("text"):
        d["maintenant"] = short(now["text"], 110)
        if now.get("why"):
            d["pourquoi"] = short(now["why"], 90)
    p = snap.sub("plan")
    if p and p.get("src") != "none" and (p.get("label") or p.get("steps")):
        bits = []
        if _i(p.get("steps")):
            bits.append(f"étape {_i(p.get('step'), 1)} sur {_i(p.get('steps'))}")
        if p.get("label"):
            bits.append(short(p["label"], 80))
        if _i(p.get("left")):
            bits.append(f"≈ {_i(p.get('left'))} min pour cette étape")
        d["trajet_en_jeu"] = ", ".join(bits)
        if _i(p.get("minutes")):
            d["duree_trajet"] = "≈ " + dur_fr(_i(p.get("minutes")) * 60)
        v = str(p.get("verdict") or "ok")
        if v != "ok":
            d["verdict"] = VERDICT_FR.get(v, v) + (f" : {short(p.get('reason'), 80)}" if p.get("reason") else "")
        if p.get("stale"):
            d["attention"] = "des étapes ne sont plus dans ton journal : le trajet doit être recalculé"
        if not d.get("maintenant") and p.get("label"):
            d["maintenant"] = short(p["label"], 110)
    else:
        first = next((r for r in _l(snap.sub("tracker").get("route")) if isinstance(r, dict)), None)
        if first and not d.get("maintenant"):
            d["maintenant"] = short(first.get("task") or (("Rends « " if first.get("ready") else "Fais « ")
                                                           + str(first.get("title")) + " »"), 110)
    app = app_plan_lines(snap)
    if app:
        d["plan_appli"] = app
    level = live_progress(snap)["level"]
    fac = _d(snap.s.get("leveling")).get("faction") or _d(snap.export.get("player")).get("faction")
    hint = _d(snap.s.get("leveling")).get("hint") or (zones_for_level(level, fac) if level else None)
    if hint:
        d["zones_de_ton_niveau"] = short(hint, 90) + " (d'après Classic)"
    return Section("trajet", d, snap.age)


def app_plan_lines(snap: Snap) -> list[str]:
    """The route the app computed (TreeAI_Plan), when it is for this character and less than 12 h old."""
    plan = snap.plan
    if not isinstance(plan, dict):
        return []
    if plan.get("char") and snap.key and plan.get("char") != snap.key:
        return []
    made = _n(plan.get("made"))
    if made and made > EPOCH_MIN and snap.now - made > 12 * 3600:
        return []
    try:
        from treeaicoach.wow.planner import routes  # noqa: PLC0415

        chosen = _d(snap.char.get("plan")).get("route") or snap.sub("plan").get("route")
        lines = [ln for ln in routes.summary_lines(plan, chosen if chosen not in ("auto", None) else None)
                 if "XP/h" not in ln]
        return [short(x, 160) for x in lines[:3]]
    except Exception:
        log.debug("app plan lines failed", exc_info=True)
        return []


# ------------------------------------------------------------------ group
def _members(snap: Snap, with_state: bool) -> list[str]:
    g = snap.sub("group")
    out: list[str] = []
    if _i(g.get("v")) == 2 and _l(g.get("members")):
        for m in _l(g.get("members"))[:5]:
            if not isinstance(m, dict) or m.get("me"):
                continue
            bits = [_label(m.get("class"), m.get("level"))]
            if with_state:
                if m.get("dead"):
                    bits.append("mort")
                elif _n(m.get("hp")) is not None and _i(m.get("hp")) < 50:
                    bits.append(f"PV {_i(m.get('hp'))} %")
                if _n(m.get("mana")) is not None and _i(m.get("mana")) < 30:
                    bits.append(f"mana {_i(m.get('mana'))} %")
                if _n(m.get("bags")) is not None and _i(m.get("bags")) <= 2:
                    bits.append("sacs pleins")
                if m.get("zone"):
                    bits.append(short(m["zone"], 30))
                if _i(m.get("leaveMin")):
                    bits.append(f"part dans {_i(m.get('leaveMin'))} min")
                if m.get("afk"):
                    bits.append("absent")
            out.append(", ".join(bits))
        return out
    team = snap.sub("team")
    if _l(team.get("members")):
        for m in _l(team.get("members"))[:5]:
            if not isinstance(m, dict):
                continue
            bits = [_label(m.get("class"), m.get("level"))]
            if m.get("role"):
                bits.append(str(ROLE_FR.get(str(m["role"]).upper(), m["role"])))
            if with_state:
                if m.get("dead"):
                    bits.append("mort")
                if m.get("far") or m.get("otherZone"):
                    bits.append("loin" if m.get("far") else "dans une autre zone")
                if m.get("greyWeapon"):
                    bits.append("arme grise")
                if _i(m.get("missingSlots")):
                    bits.append(f"{_i(m.get('missingSlots'))} emplacement(s) vide(s)")
            out.append(", ".join(bits))
        return out
    mi = snap.sub("missions")
    for m in _l(mi.get("members"))[:5]:
        if isinstance(m, dict):
            lab = _label(m.get("class"), m.get("level"))
            if snap.labels.get(str(m.get("name") or "")) != "toi":
                out.append(lab)
    if not out:
        for m in _l(snap.export.get("squad"))[:4]:
            if isinstance(m, dict):
                out.append(_label(m.get("class"), m.get("level")))
    if not out:
        for m in _l(snap.s.get("group"))[:4]:          # legacy flat list
            if isinstance(m, dict):
                out.append(_label(m.get("class") or m.get("classFR"), m.get("level"))
                           + (f" ({m['zone']})" if with_state and m.get("zone") else ""))
    return out


def group(snap: Snap) -> Section:
    """Group members (labels, never names) + their state when fresh, common quests, missions."""
    d: dict[str, Any] = {}
    g = snap.sub("group")
    g_age = snap.age
    if _n(g.get("updated")) and _n(g.get("updated")) > EPOCH_MIN:
        g_age = max(0.0, snap.now - (_n(g.get("updated")) or 0.0))
    fresh = g_age is not None and g_age <= STALE_S
    gs = _i(snap.s.get("groupSize"))
    if gs:
        d["taille"] = gs
    mem = _members(snap, with_state=fresh)
    if mem:
        d["membres"] = mem
    if fresh:
        adv = [short(a.get("text"), 100) for a in _l(g.get("advices")) if isinstance(a, dict) and a.get("text")]
        if adv:
            d["conseils"] = adv[:3]
    common = [f"{short(q.get('title'), 40)} ({_i(q.get('holders'))} l'ont, {_i(q.get('done'))} finie)"
              for q in _l(g.get("common")) if isinstance(q, dict) and q.get("title")]
    if common:
        d["quetes_communes"] = common[:4]
    mi = snap.sub("missions")
    board = []
    for b in _l(mi.get("board"))[:4]:
        if not isinstance(b, dict) or not b.get("title"):
            continue
        line = f"{short(b.get('title'), 40)} : {_i(b.get('done'))}/{_i(b.get('holders'))} prêts"
        if b.get("ready"):
            line += ", à rendre ensemble"
        elif b.get("status"):
            line += f" ({short(b['status'], 60)})"
        board.append(line)
    if board:
        d["missions"] = board
    if mi.get("next"):
        d["prochaine_mission"] = short(mi["next"], 100)
    assign = [short(x, 80) for x in _l(mi.get("assign")) if isinstance(x, str)]
    if assign:
        d["qui_fait_quoi"] = assign[:3]
    meter = _d(snap.sub("dungeon").get("meter"))
    if meter.get("top"):                         # the client's damage meter (last instance fight): a name → label
        d["degats"] = f"en tête du dernier compteur de dégâts en donjon : {meter['top']}"
    if not fresh and mem:
        d["note"] = "état des membres trop vieux pour être donné (/reload en jeu)"
    return Section("groupe", d, g_age)


# ------------------------------------------------------------------ auction house
def auction_house(snap: Snap) -> Section:
    a = snap.sub("auction")
    d: dict[str, Any] = {}
    manque: list[str] = []
    scan_age = None
    if _n(a.get("scanAge")) is not None:
        scan_age = (_n(a.get("scanAge")) or 0.0) + (snap.age or 0.0)
    try:
        from treeaicoach.wow import auction  # noqa: PLC0415

        deals = [d_ for d_ in _l(a.get("deals")) if isinstance(d_, dict)]
        if deals and (scan_age is None or scan_age <= AH_MAX_AGE_S):
            d["affaires_sures"] = [short(auction._deal_text(x), 150) for x in deals[:3]]
        elif deals:
            manque.append("les affaires de l'HV viennent d'un scan de plus de 24 h, rescanne l'HV")
        elif isinstance(a.get("none"), dict):
            why = [short(x, 80) for x in _l(a["none"].get("why")) if isinstance(x, str)]
            d["rien_de_sur"] = [auction.NONE_TEXT] + why[:3]
        sells = []
        for x in _l(a.get("sell"))[:3]:
            if not isinstance(x, dict) or not x.get("name"):
                continue
            if x.get("better") == "vendor":
                sells.append(f"{short(x['name'], 40)} : au marchand ({money_fr(x.get('vendor')) or '?'})")
            elif _i(x.get("unit")):
                sells.append(f"{short(x['name'], 40)} x{max(1, _i(x.get('count'), 1))} : à l'HV à "
                             f"{money_fr(x.get('unit'))} l'unité (net {money_fr(x.get('net')) or '?'})")
        if sells:
            d["a_vendre"] = sells
    except Exception:
        log.debug("auction section failed", exc_info=True)
    inv = snap.sub("inventory")
    if _i(inv.get("greyCount")):
        d["objets_gris"] = f"{_i(inv.get('greyCount'))} (≈ {money_fr(inv.get('greyValue')) or '?'} au marchand)"
    mats = _d(inv.get("mats"))
    if mats:
        d["composants_en_sac"] = [f"{short(k, 35)} x{_i(v)}" for k, v in list(mats.items())[:3]]
    if scan_age is not None:
        d["scan_hv"] = age_fr(scan_age)
    elif a:
        manque.append("pas encore de scan de l'HV, ouvre l'HV, clique « Scanner » puis fais /reload")
    if not a:
        manque.append("aucune donnée d'HV, ouvre l'HV en jeu, scanne puis fais /reload")
    if a.get("neutral"):
        d["commission"] = "HV neutre : 15 %"
    if manque:
        d["manque"] = manque
    return Section("hv", d, snap.age)


# ------------------------------------------------------------------ dungeons
def dungeons() -> list[dict]:
    return list(_lua_tables()["dungeons"])


_ALLIANCE_ONLY = {"STOCKS"}


def _roles(snap: Snap) -> dict[str, int]:
    out = {"tank": 0, "soigneur": 0, "dégâts": 0}
    for m in _l(snap.sub("team").get("members")):
        if isinstance(m, dict):
            r = str(ROLE_FR.get(str(m.get("role") or "").upper(), m.get("role") or ""))
            if r in out:
                out[r] += 1
    return out


def _group_levels(snap: Snap) -> list[int]:
    lv = [live_progress(snap)["level"]]
    src = _l(snap.sub("team").get("members")) or _l(snap.sub("group").get("members")) or _l(snap.sub("missions").get("members"))
    for m in src:
        if isinstance(m, dict) and not m.get("me") and _i(m.get("level")):
            if snap.labels.get(str(m.get("name") or "")) == "toi":
                continue
            lv.append(_i(m.get("level")))
    return [x for x in lv if x]


def asked_size(question: str) -> int:
    q = fold(question)
    m = re.search(r"\b(?:a|en)\s+(\d|deux|trois|quatre|cinq)\b", q)
    if re.search(r"\bduo\b", q):
        return 2
    if m:
        w = m.group(1)
        return int(w) if w.isdigit() else NUM_WORDS.get(w, 0)
    return 0


def asked_dungeon(question: str) -> dict | None:
    q = fold(question)
    best = None
    for dg in dungeons():
        names = [dg["n"]] + list(dg.get("kw") or [])
        for nm in names:
            f = fold(nm)
            core = re.sub(r"^(le |la |les |l')", "", f)
            for cand in {f, core, core.split(" de ")[-1] if " de " in core else core}:
                cand = cand.strip()
                if len(cand) >= 5 and re.search(r"(?<![a-z])" + re.escape(cand) + r"(?![a-z])", q):
                    if best is None or len(cand) > best[0]:
                        best = (len(cand), dg)
    return best[1] if best else None


def dungeon(snap: Snap, question: str = "") -> Section:
    d: dict[str, Any] = {}
    levels = _group_levels(snap)
    me = levels[0] if levels else 0
    low = min(levels) if levels else 0
    fac = _d(snap.s.get("leveling")).get("faction") or _d(snap.export.get("player")).get("faction")
    size = asked_size(question) or max(1, _i(snap.s.get("groupSize"), 1))
    table = [x for x in dungeons() if not (fac == "Horde" and x["key"] in _ALLIANCE_ONLY)]
    cur = snap.sub("dungeon")
    if cur.get("name"):
        d["prevu"] = short(cur["name"], 50) + (f" ({short(cur.get('why'), 50)})" if cur.get("why") else "")
    run = _d(cur.get("run"))
    if run:
        d["en_cours"] = (f"{_i(run.get('kills'))} boss tués, {_i(run.get('deaths'))} morts, {_i(run.get('wipes'))} wipes"
                         + (f", prochain boss : {short(cur.get('next'), 40)}" if cur.get("next") else ""))
    if me and table:
        fit = sorted((x for x in table if x["min"] <= me <= x["max"]), key=lambda x: abs((x["min"] + x["max"]) / 2 - me))
        if fit:
            d["a_ton_niveau"] = [f"{x['n']} ({x['min']}–{x['max']}{', nouveau Forever' if x['new'] else ''})" for x in fit[:5]]
        if size <= 3:
            try:
                from treeaicoach.wow.planner import models  # noqa: PLC0415

                grey = models.grey_level(low)
            except Exception:
                grey = low - 7
            small = sorted((x for x in table if x["max"] <= low and x["max"] >= grey + 3 and not x["new"]),
                           key=lambda x: -x["max"])
            if small:
                d[f"a_{size}"] = ([f"{x['n']} ({x['min']}–{x['max']})" for x in small[:3]]
                                  + ["à moins de 5, prends un donjon dont le niveau max est sous le tien (d'après Classic)"])
    ask = asked_dungeon(question)
    if ask:
        verdict = []
        if low and low < ask["min"]:
            verdict.append(f"trop tôt : il faut le niveau {ask['min']}")
        elif me and me > ask["max"] + 2:
            verdict.append("vous êtes au-dessus : peu d'XP")
        r = _roles(snap)
        if size >= 4 and snap.sub("team"):
            if not r["tank"]:
                verdict.append("pas de tank dans le groupe")
            if not r["soigneur"]:
                verdict.append("pas de soigneur dans le groupe")
        d["demande"] = f"{ask['n']} ({ask['min']}–{ask['max']}) : " + ("; ".join(verdict) if verdict else "niveau OK")
    if levels and len(levels) > 1:
        d["niveaux_du_groupe"] = ", ".join(str(x) for x in levels)
    r = _roles(snap)
    if snap.sub("team"):
        d["roles"] = f"{r['tank']} tank, {r['soigneur']} soigneur, {r['dégâts']} dégâts + toi"
    dq = []
    for q in _l(snap.sub("quests").get("list")):
        if isinstance(q, dict) and (q.get("dungeon") or q.get("groupTag")) and q.get("title"):
            dq.append(short(q["title"], 40))
    for q in _l(snap.export.get("quests")):
        if isinstance(q, dict) and _i(q.get("dg")) and q.get("st", "log") == "log" and q.get("t"):
            dq.append(short(q["t"], 40))
    if dq:
        d["quetes_de_donjon"] = list(dict.fromkeys(dq))[:4]
    if table:
        d["source"] = "niveau min du client Forever, niveau max d'après Classic"
    else:
        d["manque"] = ["liste des donjons introuvable"]
    return Section("donjon", d, snap.age)


# ------------------------------------------------------------------ pet, ammo, best pet
def pet(snap: Snap) -> Section:
    h = snap.sub("hunter")
    d: dict[str, Any] = {}
    p = _d(h.get("pet")) or _d(snap.s.get("pet"))
    level = live_progress(snap)["level"]
    if p:
        bits = [str(p.get("family") or "Familier")]
        if _i(p.get("level")):
            bits.append(f"niveau {_i(p.get('level'))}" + (f" (toi {level})" if level else ""))
        if p.get("dead"):
            bits.append("mort")
        hap = HAPPINESS_FR.get(_i(p.get("happiness")))
        if hap:
            bits.append(hap)
        if p.get("loyalty"):
            bits.append(short(p["loyalty"], 30).lower())
        d["familier"] = ", ".join(bits)
        if _i(p.get("tpFree")):
            d["points_de_dressage_libres"] = _i(p.get("tpFree"))
        food = _i(p.get("food"))
        if food:
            name = None
            gd = _optional("gamedata")
            try:
                name = gd.item_name(food) if gd is not None else None
            except Exception:
                name = None
            d["nourriture_en_sac"] = name or "oui"
        elif p.get("diet"):
            d["nourriture_en_sac"] = "aucune qu'il aime dans tes sacs"
    elif str(snap.s.get("class") or "").upper() == "HUNTER":
        d["familier"] = "pas de familier au dernier /reload"
    elif snap.s.get("class"):
        d["familier"] = "TreeAI ne suit que les familiers de chasseur"
    a = _d(h.get("ammo"))
    n_ammo = max(_i(a.get("total")), _i(a.get("equipped")))
    if a:
        kind = {"arrow": "flèches", "bullet": "balles"}.get(str(a.get("kind") or ""), "munitions")
        txt = f"{num_fr(n_ammo)} {kind}"
        per_h = _n(_d(snap.sub("stats").get("perH")).get("ammo"))
        if per_h and per_h > 0:
            txt += f", ≈ {dur_fr(n_ammo * 3600.0 / per_h)} de jeu à ton rythme"
        need = _i(snap.sub("hunterPro").get("ammoNeed"))
        if need:
            txt += f" (il en faut ≈ {num_fr(need)} pour finir le niveau)"
        if a.get("mismatch"):
            txt += " ; elles ne vont pas dans ton arme"
        d["munitions"] = txt
    if not d:
        return Section("familier", {}, snap.age)
    return Section("familier", d, snap.age)


def best_pet(snap: Snap, limit: int = 3) -> Section:
    """Beasts worth taming near the player's level (client pet table + Classic beast list ≈)."""
    d: dict[str, Any] = {}
    level = live_progress(snap)["level"]
    fac = _d(snap.s.get("leveling")).get("faction") or _d(snap.export.get("player")).get("faction")
    try:
        from treeaicoach.wow import gamedata  # noqa: PLC0415

        data = gamedata._load("pets")         # cached client pet table + beast list
    except Exception:
        data = {}
    fams = _d(data.get("families"))
    cur = _d(_d(snap.sub("hunter")).get("pet"))
    if cur.get("family"):
        d["actuel"] = f"{cur['family']} niveau {_i(cur.get('level'))}" if _i(cur.get("level")) else str(cur["family"])
    letter = {"Horde": "H", "Alliance": "A"}.get(str(fac or ""), "")
    zones = zone_bands()
    cands = []
    for b in _l(data.get("beasts")):
        if not isinstance(b, dict) or not b.get("n") or b.get("rare"):
            continue
        lo, hi = _i(b.get("lo")), _i(b.get("hi")) or _i(b.get("lo"))
        if not level or hi > level or hi < level - 4:
            continue
        if letter and b.get("fz") not in (None, "", letter):
            continue
        fam = _d(fams.get(str(b.get("fam"))))
        score = _i(fam.get("dmg")) + 0.4 * _i(fam.get("armor")) + 0.3 * _i(fam.get("hp")) + 2 * (hi - level + 4)
        teach = b.get("teach") if isinstance(b.get("teach"), list) and len(b["teach"]) == 2 else None
        if teach:
            score += 6
        band = zones.get(_i(b.get("map")))
        where = short(b.get("zone") or (band[2] if band else "?"), 30)
        txt = f"{short(b['n'], 30)} ({fam.get('n') or '?'}, niv {lo}{'–' + str(hi) if hi != lo else ''}, {where})"
        if teach:
            txt += f", apprend {teach[0]} rang {teach[1]}"
        cands.append((score, txt))
    if cands:
        d["a_apprivoiser"] = [t for _s, t in sorted(cands, key=lambda x: -x[0])[:limit]]
        d["source"] = "liste de bêtes d'après Classic : vérifie en jeu avant d'y aller"
    else:
        d["manque"] = ["pas de bête connue à ton niveau dans mes données"]
    return Section("meilleur_familier", d, None)


# ------------------------------------------------------------------ errors / blocked actions
def errors(snap: Snap) -> Section:
    diag = _d(snap.db.get("diag"))
    d: dict[str, Any] = {}
    errs = [e for e in _l(diag.get("errors")) if isinstance(e, dict)]
    if errs:
        total = sum(max(1, _i(e.get("n"), 1)) for e in errs)
        last = max(errs, key=lambda e: _i(e.get("t")))
        kinds = "1 erreur" if len(errs) == 1 else f"{len(errs)} erreurs différentes"
        seen = f"vue {total} fois" if len(errs) == 1 else f"vues {total} fois en tout"
        d["erreurs_lua"] = f"{kinds}, {seen}, la plus récente dans le module {short(last.get('module') or '?', 25)}"
        if last.get("msg"):
            d["derniere_erreur"] = short(last.get("msg"), 90)
    top = [e for e in _d(diag.get("blockedTop")).values() if isinstance(e, dict)]
    blocked = [e for e in _l(diag.get("blocked")) if isinstance(e, dict)]
    n_blk = _i(diag.get("blockedTotal")) or sum(_i(e.get("n"), 1) for e in top) or len(blocked)
    if n_blk:
        worst = max(top, key=lambda e: _i(e.get("n"))) if top else (blocked[-1] if blocked else {})
        txt = f"{n_blk} action(s) bloquée(s) par le jeu"
        if worst:
            txt += f", surtout {short(worst.get('func'), 40)} pendant {short(worst.get('ctx') or '?', 30)}"
        d["actions_bloquees"] = txt
    st = _d(diag.get("selftest"))
    if st:
        d["autotest"] = (f"{_i(st.get('ok'))} ok, {_i(st.get('warn'))} à vérifier, {_i(st.get('ko'))} en échec"
                         + (f" ({age_fr(snap.now - _n(st.get('t')))})" if _n(st.get("t")) and _n(st.get("t")) > EPOCH_MIN else ""))
    if not errs and not n_blk:
        d["etat"] = "aucune erreur ni action bloquée notée par l'addon"
    else:
        d["que_faire"] = "en jeu /tai erreurs puis /reload : le fichier TreeAI-erreurs.txt est sur la page WoW de l'appli"
    return Section("erreurs", d, snap.age)


# ------------------------------------------------------------------ « ça vaut le coup d'aller à X ? »
_ARTICLES = r"^(?:les |le |la |l'|aux |au |a la |a l'|du |des |de la |de l'|de )"
_ZONE_CACHE: dict[str, Any] = {}


def _zone_names() -> list[tuple[str, int, str, int, int]]:
    """[(folded name, area id, display name, parent area, continent)] from the client's AreaTable (zones.json)."""
    if "z" in _ZONE_CACHE:
        return _ZONE_CACHE["z"]
    out: list[tuple[str, int, str, int, int]] = []
    try:
        from treeaicoach.wow import gamedata  # noqa: PLC0415

        z = _d(gamedata._load("zones").get("zones"))
        for aid, row in z.items():
            if not isinstance(row, list) or not row:
                continue
            name = re.sub(r"\s*\([^)]*\)", "", str(row[0])).strip()
            f = re.sub(_ARTICLES, "", fold(name)).strip()
            if len(f) >= 5:
                out.append((f, _i(aid), name, _i(row[1]) if len(row) > 1 else 0, _i(row[2]) if len(row) > 2 else 0))
    except Exception:
        log.debug("zone names unavailable", exc_info=True)
    _ZONE_CACHE["z"] = out
    return out


def find_destination(question: str) -> dict | None:
    """The zone / sub-zone named in the question (« aux Salines » → Les Salines, parent Mille pointes)."""
    q = " " + re.sub(r"[^a-z0-9' ]+", " ", fold(question)) + " "
    best = None
    for f, aid, name, parent, cont in _zone_names():
        if re.search(r"(?<![a-z])" + re.escape(f) + r"(?![a-z])", q):
            score = (len(f), parent == 0)
            if best is None or score > best[0]:
                best = (score, {"area": aid, "name": name, "parent": parent, "continent": cont})
    if best is None:
        return None
    dest = best[1]
    if dest["parent"]:
        for _f, aid, name, _p, _c in _zone_names():
            if aid == dest["parent"]:
                dest["parent_name"] = name
                break
    return dest


def asked_quest_count(question: str) -> int:
    q = fold(question)
    m = re.search(r"\b(\d+|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix)\s+(?:petites?\s+)?quetes?\b", q)
    if not m:
        return 0
    w = m.group(1)
    return int(w) if w.isdigit() else NUM_WORDS.get(w, 0)


def _map_by_name(name: str) -> int | None:
    try:
        from treeaicoach.wow.planner import data as pdata  # noqa: PLC0415

        f = fold(name)
        for mid, row in _d(pdata.taxi_data().get("maps")).items():
            if isinstance(row, list) and row and fold(row[0]) == f:
                return int(mid)
    except Exception:
        log.debug("map lookup failed", exc_info=True)
    return None


def detour(snap: Snap, question: str, xp_section: Section | None = None) -> Section | None:
    """Travel time vs XP of the quests there vs what the player makes here: the « pro » sum, deterministic."""
    dest = find_destination(question)
    if dest is None:
        return None
    d: dict[str, Any] = {"destination": dest["name"] + (f" ({dest['parent_name']})" if dest.get("parent_name") else "")}
    manque: list[str] = []
    try:
        from treeaicoach.wow.planner import data as pdata  # noqa: PLC0415
        from treeaicoach.wow.planner import models, transport  # noqa: PLC0415
    except Exception:
        return Section("calcul_detour", {**d, "manque": ["calcul de trajet indisponible"]})
    ex = snap.export
    pl = _d(ex.get("player"))
    level = live_progress(snap)["level"] or _i(pl.get("level"))
    cls = str(snap.s.get("class") or pl.get("class") or "")
    rects = pdata.map_rects(_l(ex.get("maps")))
    zone_name = dest.get("parent_name") or dest["name"]
    dmap = _map_by_name(zone_name)
    band = zone_bands().get(dmap or -1)
    if band:
        d["niveau_de_la_zone"] = f"{band[0]}–{band[1]} (d'après Classic)"
    # the quests there (my log): the export's own quest zone, else objectives / finisher on that map
    here_q = []
    for q in _l(ex.get("quests")):
        if not isinstance(q, dict) or q.get("st", "log") != "log":
            continue
        pts = [p for p in [q.get("f")] + _l(q.get("o")) if isinstance(p, dict) and _i(p.get("map")) == dmap]
        if (dmap and _i(q.get("zone")) == dmap) or pts:
            here_q.append((q, pts))
    n_asked = asked_quest_count(question)
    n = n_asked or len(here_q)
    if not n:
        manque.append("je ne vois pas de quête de ton journal là-bas, dis-moi combien de quêtes")
    d["quetes"] = n
    # destination point: centroid of those quests' points, else the zone map centre (approximate)
    approx = False
    dest_pt = None
    pts = [p for _q, ps in here_q for p in ps if _n(p.get("x")) is not None]
    if pts and dmap in rects:
        ws = [rects[dmap].world(_n(p["x"]) or 50.0, _n(p["y"]) or 50.0) for p in pts]
        dest_pt = transport.Place(ws[0][0], sum(w[1] for w in ws) / len(ws), sum(w[2] for w in ws) / len(ws))
    elif dmap in rects:
        c, x, y = rects[dmap].world(50.0, 50.0)
        dest_pt, approx = transport.Place(c, x, y), True
    here_pt = None
    if _i(pl.get("map")) in rects and _n(pl.get("x")) is not None:
        c, x, y = rects[_i(pl["map"])].world(_n(pl.get("x")) or 50.0, _n(pl.get("y")) or 50.0)
        here_pt = transport.Place(c, x, y)
    trip_s = None
    if dest_pt is not None and here_pt is not None:
        known = [_i(t) for t in _l(pl.get("taxi")) if _i(t)]
        speed = _n(pl.get("mount")) or _n(pl.get("speed")) or transport.WALK
        trip = transport.fastest(here_pt, dest_pt, faction=pl.get("faction"), taxi=pdata.taxi_data(), known=known,
                                 speed=max(transport.WALK, speed))
        if math.isfinite(trip.seconds):
            trip_s = trip.seconds
            legs = [lg for lg in trip.legs if lg.mode != "walk" or lg.seconds >= 90] or trip.legs
            how = ", ".join(transport.leg_label(lg) for lg in legs[:5]) + (", …" if len(legs) > 5 else "")
            d["trajet_aller"] = "≈ " + dur_fr(trip_s) + (" : " + how if any(lg.mode != "walk" for lg in trip.legs)
                                                         else " à pied")
            if approx:
                d["precision"] = "trajet vers le centre de la zone (position des quêtes inconnue) : approximatif"
        else:
            manque.append("pas de chemin connu jusque-là")
    else:
        manque.append("ta position ou celle de la zone manque (fais /reload en jeu)")
    # XP of the quests there + time on site (planner models, learned speed when known)
    xp_total, xp_est = 0, False
    onsite, onsite_est = 0.0, False
    calib = None
    try:
        calib = models.calib_from_learn(_d(snap.db.get("learn")), _d(snap.char.get("learn")))
    except Exception:
        calib = None
    tr = {_i(r.get("id")): r for r in _l(snap.sub("tracker").get("route")) if isinstance(r, dict)}
    chosen = here_q[:n] if here_q else []
    for q, _pts in chosen:
        ql = _i(q.get("lv")) or level
        base = _i(q.get("xp"))
        if base > 0:
            xp_total += models.quest_xp(base, level, ql)
        else:
            xp_total += models.quest_xp(models.quest_xp_estimate(ql), level, ql)
            xp_est = True
        sec = _i(tr.get(_i(q.get("id")), {}).get("sec"))
        if sec:
            onsite += sec
        else:
            t = sum(models.objective_time(o, cls, level, calib).median for o in _l(q.get("o")) if isinstance(o, dict))
            onsite += t * (calib.eff if calib else 1.6) + 120.0 if t else 480.0
            onsite_est = True
    missing = max(0, n - len(chosen))
    if missing:
        ql = band[0] + 3 if band else level
        xp_total += missing * models.quest_xp(models.quest_xp_estimate(ql), level, ql)
        onsite += missing * 480.0
        xp_est = onsite_est = True
    if n:
        d["xp_des_quetes"] = f"≈ {num_fr(round(xp_total / 100.0) * 100)} XP" + (" (estimation)" if xp_est else "")
        d["temps_sur_place"] = "≈ " + dur_fr(onsite) + (" (estimation)" if onsite_est else "")
    here_xph, guess = None, False
    xs = xp_section.data if xp_section is not None else {}
    if xs.get("xp_par_heure"):
        here_xph = _n(str(xs["xp_par_heure"]).replace(" ", ""))
        d["xp_par_heure_ici"] = f"{xs['xp_par_heure']} XP/h ({xs.get('source', '?')})"
    elif level:
        try:  # no measured XP/h: compare with killing mobs of your level (Classic formulas), said as an estimate
            grind = models.kill_xp(level, level) * 3600.0 / max(1.0, models.kill_cycle(cls, level, level, calib))
            if grind > 0:
                here_xph, guess = grind, True
                d["xp_par_heure_repere"] = (f"≈ {num_fr(round(grind / 100.0) * 100)} XP/h en tuant des monstres de ton "
                                            "niveau (estimation, ton XP par heure n'est pas encore mesurée)")
        except Exception:
            log.debug("grind baseline failed", exc_info=True)
    stay = bool(band and band[0] <= level <= band[1] and str(snap.sub("plan").get("verdict")) == "zone")
    if trip_s is not None and n:
        back = 0.0 if stay else trip_s
        total = trip_s + onsite + back
        detour_xph = xp_total * 3600.0 / max(60.0, total)
        d["temps_total"] = "≈ " + dur_fr(total) + ("" if back == 0 else " avec le retour")
        d["xp_par_heure_du_detour"] = f"≈ {num_fr(round(detour_xph / 100.0) * 100)} XP/h"
        if here_xph:
            ratio = detour_xph / here_xph
            maybe = "probablement " if guess else ""
            if ratio >= 1.0:
                d["verdict"] = f"{maybe}vaut le coup : le détour rapporte plus que ce que tu fais ici"
            elif ratio >= 0.8:
                d["verdict"] = "à peu près pareil : vas-y seulement si tu y passes de toute façon"
            elif stay:
                d["verdict"] = (f"{maybe}pas rentable pour ces quêtes seules, mais la zone est à ton niveau : "
                                "vas-y quand tu changes de zone")
            else:
                d["verdict"] = f"{maybe}pas rentable maintenant : garde ces quêtes pour quand tu iras dans cette zone"
        else:
            manque.append("ton XP par heure actuelle n'est pas mesurée, je ne peux pas comparer")
    if manque:
        d["manque"] = manque
    return Section("calcul_detour", d, None if snap.live else snap.age)


# ------------------------------------------------------------------ talents / gear / spells (existing helpers)
def talents(snap: Snap) -> Section | None:
    try:
        from treeaicoach.wow import talents as tal  # noqa: PLC0415

        v = tal.talents_context(snap.s.get("className") or snap.s.get("class"), live_progress(snap)["level"],
                                snap.s.get("talents"))
        return Section("talents", v, snap.age) if v else None
    except Exception:
        log.debug("talents section failed", exc_info=True)
        return None


def gear(snap: Snap) -> Section | None:
    try:
        from treeaicoach.wow import gear as gr  # noqa: PLC0415

        v = gr.gear_context({"summary": snap.s})
        return Section("equipement", v, snap.age) if v else None
    except Exception:
        log.debug("gear section failed", exc_info=True)
        return None


def spells(snap: Snap) -> Section | None:
    try:
        from treeaicoach.wow import gamedata  # noqa: PLC0415

        v = gamedata.game_data_context(snap.s.get("className") or snap.s.get("class"), live_progress(snap)["level"])
        if not v:
            return None
        tr = snap.sub("errands").get("trainer")
        if isinstance(tr, (str, dict)):
            v = {**v, "maitre": short(tr if isinstance(tr, str) else (tr.get("name") or tr.get("text")), 60)}
        return Section("sorts", v, None)
    except Exception:
        log.debug("spells section failed", exc_info=True)
        return None


def combat(snap: Snap) -> Section | None:
    if not snap.facts:
        return None
    return Section("combat_recent", snap.facts[-6:], None, volatile=False)


def freshness(snap: Snap) -> str:
    """One honest sentence about where the data comes from and how old it is."""
    if not snap.s:
        return "aucune donnée de l'addon : connecte-toi en jeu puis tape /reload"
    bits = []
    if snap.age is None:
        bits.append("âge de la sauvegarde de l'addon inconnu")
    elif snap.age <= STALE_S:
        bits.append(f"sauvegarde de l'addon {age_fr(snap.age)}")
    else:
        bits.append(f"sauvegarde de l'addon {age_fr(snap.age)} : quêtes et état peut-être dépassés (/reload pour rafraîchir)")
    if snap.live:
        bits.append("XP, niveau et zone en direct par le journal de discussion")
    return " ; ".join(bits)
