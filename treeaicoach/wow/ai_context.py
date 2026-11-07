"""WoW-expert AI context: « Hey coach » / Discord questions asked while TreeAI is in WoW mode.

The question goes to a French World of Warcraft: Forever expert prompt, with a compact context built
from the game's OWN files only (Blizzard-allowed channels): the addon's SavedVariables ``TreeAIDB``
(class, level, zone, spec, gold, bags, quests… written on /reload / logout) and live facts from
``Logs/WoWCombatLog.txt``. No memory / screen / packet reading.

Interface looked up LAZILY (other modules may be missing; every lookup has a fallback):
  - ``treeaicoach.wow.wow_mode_active() -> bool``        (else engine ``_wow_mode`` / ``wow_mode``, else False)
  - ``treeaicoach.wow.savedvars.load_treeai_db() -> dict | None``  (also tried: ``load_db``, ``read_db``,
    ``load_savedvars``; else engine ``_wow_db`` / ``wow_db``)
  - ``treeaicoach.wow.coach.recent_facts(limit) -> list[str]``      (also tried on ``treeaicoach.wow.combatlog``
    and on engine ``_wow_coach``)

Pure functions, never raise (``log.debug`` on errors) except :func:`wow_answer` (raises ``AIError``
like ``ai_advisor.general_answer``).
"""

from __future__ import annotations

import importlib
import json
import logging
import re
import time
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

#: System prompt: WoW Forever expert, French, short spoken answers, ToS-safe.
WOW_PROMPT = (
    "Tu es TreeAI, l'assistant vocal d'un joueur de World of Warcraft: Forever (client de la famille "
    "Classic, niveau max 60, double spécialisation au niveau 40, points d'Héritage, camping, 9 classes "
    "retravaillées, client français), qui joue en groupe avec des amis et parle dans un salon vocal "
    "Discord. Tu es un expert de WoW Classic et Forever : classes, talents, rotations, macros, quêtes, "
    "zones et niveaux, donjons, métiers, familiers de chasseur, économie. Utilise les noms français du "
    "jeu. Tu reçois en JSON l'état du personnage (fichiers du jeu : addon TreeAI et journal de combat) : "
    "si la question porte dessus, réponds précisément avec ces faits ; sinon réponds simplement. "
    "On peut te poser n'importe quelle question, même hors WoW ; mais dès que c'est utile, relie ta "
    "réponse aux quêtes en cours du joueur et de ses amis (quetes, groupe_treeai, missions : qui a quoi, "
    "ce qui reste, l'ordre conseillé). Si on te demande quoi faire (« on fait quoi ? ») et que « trajet » est "
    "présent, réponds avec le trajet choisi : sa prochaine étape dite simplement, verbe d'abord, avec le "
    "lieu et le temps en mots de tous les jours (« Va tuer 6 loups au nord du lac, ça prend 3 minutes »). "
    "Ne parle jamais d'XP par minute, de score, de pourcentage de rentabilité ni de modèle de coût. "
    "Ne parle jamais de League of Legends sauf si on te le demande. "
    "Réponds en français, tutoiement, en 1 à 3 phrases courtes faites pour être dites à voix haute : "
    "pas de liste, pas de markdown, pas d'emoji. Si une valeur exacte de Forever t'est inconnue, dis-le "
    "en une phrase au lieu d'inventer. Ne propose jamais de bot, d'automatisation ou de logiciel "
    "interdit par Blizzard ; une macro doit lancer une seule technique par touche."
)

#: Hotkey « Demander à l'IA » without a spoken question, in WoW mode.
DEFAULT_QUESTION = "Quel est le conseil le plus utile pour moi maintenant ?"

_DB_CACHE: dict[str, Any] = {"t": -1e9, "db": None}
_DB_TTL_S = 15.0
_MAX_QUESTS = 8
_MAX_FACTS = 8


# ------------------------------------------------------------------ lazy lookups
def _lookup(module: str, *names: str) -> Callable[..., Any] | None:
    """First callable ``module.<name>`` found, else None (module missing: None). Never raises."""
    try:
        mod = importlib.import_module(module)
    except Exception:
        return None
    for n in names:
        fn = getattr(mod, n, None)
        if callable(fn):
            return fn
    return None


def wow_mode_active(engine: Any = None) -> bool:
    """True while TreeAI runs in WoW mode (WoW running, LoL not). Never raises."""
    fn = _lookup("treeaicoach.wow", "wow_mode_active")
    if fn is not None:
        try:
            return bool(fn())
        except Exception:
            log.debug("wow_mode_active failed", exc_info=True)
    for attr in ("_wow_mode", "wow_mode"):
        v = getattr(engine, attr, None)
        if v is not None and not callable(v):
            return bool(v)
        if callable(v):
            try:
                return bool(v())
            except Exception:
                log.debug("engine wow_mode failed", exc_info=True)
    return False


def load_db(engine: Any = None, now: float | None = None) -> dict[str, Any] | None:
    """The addon's ``TreeAIDB`` (parsed SavedVariables), cached 15 s, else None. Never raises."""
    db = getattr(engine, "_wow_db", None) or getattr(engine, "wow_db", None)
    if isinstance(db, dict):
        return db
    t = time.monotonic() if now is None else now
    if t - _DB_CACHE["t"] < _DB_TTL_S:
        return _DB_CACHE["db"]
    fn = _lookup("treeaicoach.wow.savedvars", "load_treeai_db", "load_db", "read_db", "load_savedvars")
    out = None
    if fn is not None:
        try:
            out = fn()
            if isinstance(out, dict) and isinstance(out.get("TreeAIDB"), dict):
                out = out["TreeAIDB"]
            out = out if isinstance(out, dict) else None
        except Exception:
            log.debug("TreeAIDB load failed", exc_info=True)
    _DB_CACHE.update(t=t, db=out)
    return out


def live_facts(engine: Any = None, limit: int = _MAX_FACTS) -> list[str]:
    """Recent combat-log facts (« Familier mort il y a 40 s »…), newest last. Never raises."""
    fns: list[Callable[..., Any]] = []
    obj = getattr(engine, "_wow_coach", None)
    if callable(getattr(obj, "recent_facts", None)):
        fns.append(obj.recent_facts)
    for mod in ("treeaicoach.wow.coach", "treeaicoach.wow.combatlog"):
        fn = _lookup(mod, "recent_facts")
        if fn is not None:
            fns.append(fn)
    for fn in fns:
        try:
            raw = fn(limit)
        except TypeError:
            try:
                raw = fn()
            except Exception:
                continue
        except Exception:
            log.debug("recent_facts failed", exc_info=True)
            continue
        facts = [_short(_fact_text(f), 140) for f in (raw or ())]
        facts = [f for f in facts if f]
        if facts:
            return facts[-limit:]
    return []


# ------------------------------------------------------------------ TreeAIDB extraction (schema-tolerant)
def _short(text: Any, limit: int) -> str:
    s = " ".join(str(text if text is not None else "").split())
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def _fact_text(f: Any) -> str:
    if isinstance(f, dict):
        return str(f.get("text") or f.get("msg") or f.get("fact") or "")
    return str(f or "")


def _pick(d: Any, *keys: str) -> Any:
    """``d[key]`` for the first present key (case-insensitive), else None."""
    if not isinstance(d, dict):
        return None
    low = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        v = low.get(k.lower())
        if v not in (None, ""):
            return v
    return None


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _stamp(c: Any) -> float:
    return _num(_pick(c, "updated", "lastSeen", "last_seen", "time", "ts", "saved")) or 0.0


def current_character(db: Any) -> dict[str, Any] | None:
    """The character entry of ``TreeAIDB``: ``char``/``character``/``player``, else the most recently
    updated entry of ``chars``/``characters``/``profiles``, else the flat table itself."""
    if not isinstance(db, dict):
        return None
    c = _pick(db, "char", "character", "player", "me", "current")
    if isinstance(c, dict):
        return c
    many = _pick(db, "chars", "characters", "profiles", "toons")
    if isinstance(many, dict):
        many = list(many.values())
    if isinstance(many, list):
        entries = [e for e in many if isinstance(e, dict)]
        if entries:
            return max(entries, key=_stamp)
    if _pick(db, "class", "level", "zone") is not None:
        return db
    return None


def money_text(copper: Any) -> str | None:
    """Copper -> « 12 po 34 pa 5 pc » (French coin names)."""
    c = _num(copper)
    if c is None or c < 0:
        return None
    c = int(c)
    g, s, cp = c // 10000, (c // 100) % 100, c % 100
    parts = [f"{g} po"] if g else []
    if s or (g and cp):
        parts.append(f"{s} pa")
    if cp or not parts:
        parts.append(f"{cp} pc")
    return " ".join(parts)


def _spec(c: dict[str, Any]) -> str | None:
    s = _pick(c, "spec", "talents", "build")
    if isinstance(s, dict):
        pts = {str(k): int(_num(v) or 0) for k, v in s.items() if _num(v) is not None}
        if pts:
            return "/".join(str(v) for v in pts.values()) + " (" + ", ".join(pts) + ")"
        s = _pick(s, "name", "text")
    if isinstance(s, list):
        s = "/".join(str(x) for x in s)
    return _short(s, 60) if s else None


def _bags(c: dict[str, Any]) -> str | None:
    b = _pick(c, "bags", "bag", "inventory")
    free = _num(_pick(b, "free", "freeSlots", "empty")) if isinstance(b, dict) else None
    total = _num(_pick(b, "total", "totalSlots", "slots", "size")) if isinstance(b, dict) else None
    free = free if free is not None else _num(_pick(c, "freeSlots", "bagFree"))
    total = total if total is not None else _num(_pick(c, "totalSlots", "bagTotal"))
    if free is None:
        return None
    return f"{int(free)} libres" + (f" sur {int(total)}" if total else "")


def _quest(q: Any) -> str | None:
    if isinstance(q, str):
        return _short(q, 80)
    if not isinstance(q, dict):
        return None
    title = _pick(q, "title", "name", "quest")
    if not title:
        return None
    s = str(title)
    lvl = _num(_pick(q, "level", "lvl"))
    if lvl:
        s += f" [{int(lvl)}]"
    if _pick(q, "done", "complete", "completed", "isComplete") in (True, 1, "1"):
        return _short(s + " (terminée)", 90)
    objs = _pick(q, "objectives", "obj")
    if isinstance(objs, dict):
        objs = list(objs.values())
    if isinstance(objs, list):
        todo = []
        for o in objs:
            if isinstance(o, dict):
                if _pick(o, "done", "finished", "complete") in (True, 1, "1"):
                    continue
                o = _pick(o, "text", "desc", "name")
            if o:
                todo.append(str(o))
        if todo:
            s += " : " + "; ".join(todo[:3])
    return _short(s, 120)


def _quests(c: dict[str, Any], db: dict[str, Any]) -> list[str]:
    qs = _pick(c, "quests", "questLog", "quest_log")
    if qs is None:
        qs = _pick(db, "quests", "questLog")
    if isinstance(qs, dict):
        qs = list(qs.values())
    if not isinstance(qs, list):
        return []
    return [x for x in map(_quest, qs) if x][:_MAX_QUESTS]


def _pet(c: dict[str, Any]) -> dict[str, Any] | None:
    p = _pick(c, "pet")
    if not isinstance(p, dict):
        return None
    out = {k: p[k] for k in ("family", "level", "happiness", "loyalty", "health") if p.get(k) not in (None, "")}
    return out or None


def _group(db: dict[str, Any], c: dict[str, Any]) -> list[str]:
    """Group mates as « Chasseur 25 (Hautebrande) » — no names sent."""
    g = _pick(c, "group", "party") or _pick(db, "group", "party")
    if isinstance(g, dict):
        g = _pick(g, "members") or list(g.values())
    out = []
    for m in g if isinstance(g, list) else ():
        if not isinstance(m, dict):
            continue
        cls, lvl, zone = _pick(m, "class", "classFR"), _num(_pick(m, "level")), _pick(m, "zone")
        s = " ".join(x for x in (str(cls or "?"), str(int(lvl)) if lvl else "") if x)
        out.append(s + (f" ({zone})" if zone else ""))
    return out[:4]


def _squad(c: dict[str, Any]) -> dict[str, Any] | None:
    """Group brain snapshot (addon module Squad, ``summary.group`` v2) for « on fait quoi ? » at 4.
    Friends' names are replaced by « Classe niveau » labels (no names sent)."""
    s = _pick(c, "summary")
    g = _pick(s, "group") if isinstance(s, dict) else None
    g = g if isinstance(g, dict) else _pick(c, "group")
    if not isinstance(g, dict) or _num(g.get("v")) != 2 or not isinstance(g.get("members"), list):
        return None
    labels: dict[str, str] = {}
    members = []
    for m in g["members"][:5]:
        if not isinstance(m, dict):
            continue
        lvl = _num(m.get("level"))
        label = " ".join(x for x in (str(m.get("class") or "?"), str(int(lvl)) if lvl else "") if x)
        if m.get("me"):
            label += " (moi)"
        if m.get("name"):
            labels[str(m["name"])] = label
        bits = [label]
        for key, fmt_ in (("zone", "{}"), ("hp", "PV {}%"), ("mana", "mana {}%"), ("bags", "sacs libres {}"),
                          ("leaveMin", "part dans {} min")):
            if m.get(key) not in (None, ""):
                bits.append(fmt_.format(m[key]))
        if m.get("dead"):
            bits.append("mort")
        members.append(_short(", ".join(bits), 120))

    def anon(text: Any) -> str:
        t = str(text or "")
        for name in sorted(labels, key=len, reverse=True):
            t = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", labels[name], t)
        return _short(t, 140)

    out: dict[str, Any] = {"membres": members}
    adv = [anon(a.get("text")) for a in g.get("advices") or [] if isinstance(a, dict) and a.get("text")]
    if adv:
        out["conseils"] = adv[:6]
    common = [f"{_short(q.get('title'), 40)} ({q.get('holders')}/{len(members)}, {q.get('done')} fini)"
              for q in g.get("common") or [] if isinstance(q, dict) and q.get("title")]
    if common:
        out["quetes_communes"] = common[:6]
    if _num(g.get("xph")):
        out["xp_h_par_joueur"] = int(_num(g.get("xph")) or 0)
    return out


def _missions(c: dict[str, Any]) -> dict[str, Any] | None:
    """Group missions snapshot (addon module Missions, ``summary.missions`` v1) for « Hey coach, on en est
    où ? »: board, next mission, who does what, session goal/recap. Names → « Classe niveau » labels."""
    s = _pick(c, "summary")
    m = _pick(s, "missions") if isinstance(s, dict) else None
    if not isinstance(m, dict) or _num(m.get("v")) != 1:
        return None
    labels: dict[str, str] = {}
    for mem in (m.get("members") or []) if isinstance(m.get("members"), list) else []:
        if isinstance(mem, dict) and mem.get("name"):
            lvl = _num(mem.get("level"))
            labels[str(mem["name"])] = " ".join(x for x in (str(mem.get("class") or "?"), str(int(lvl)) if lvl else "") if x)

    def anon(text: Any, limit: int = 140) -> str:
        t = str(text or "")
        for name in sorted(labels, key=len, reverse=True):
            t = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", labels[name], t)
        return _short(t, limit)

    def texts(v: Any, n: int, limit: int = 140) -> list[str]:
        return [anon(x, limit) for x in (v if isinstance(v, list) else []) if isinstance(x, (str, int, float))][:n]

    out: dict[str, Any] = {}
    board = []
    for b in m.get("board") or []:
        if not isinstance(b, dict) or not b.get("title"):
            continue
        line = f"{_short(b.get('title'), 40)} : {b.get('done', 0)}/{b.get('holders', 0)} prêts, {b.get('pct', 0)} %"
        if b.get("ready"):
            line += " (à rendre ensemble)"
        elif b.get("status"):
            line += " — " + anon(b["status"], 100)
        board.append(_short(line, 160))
    if board:
        out["missions"] = board[:8]
    if m.get("next"):
        out["prochaine"] = anon(m["next"])
    for key, src, n in (("repartition", "assign", 5), ("voyage", "travel", 5), ("artisans", "crafts", 5)):
        v = texts(m.get(src), n)
        if v:
            out[key] = v
    claims = [f"{anon(x.get('by'), 30)} : {_short(x.get('quest'), 40)}" for x in m.get("claims") or [] if isinstance(x, dict)]
    if claims:
        out["pris_en_charge"] = claims[:5]
    if _num(m.get("noAddon")):
        out["membres_sans_treeai"] = int(_num(m.get("noAddon")) or 0)
    ses = m.get("session")
    if isinstance(ses, dict):
        r: dict[str, Any] = {}
        dur = _num(ses.get("duration"))
        if dur:
            r["duree_min"] = int(dur // 60)
        for key, src in (("quetes_rendues", "turned"), ("missions_terminees", "missions"), ("abandons", "abandoned"),
                         ("morts", "deaths")):
            if _num(ses.get(src)) is not None:
                r[key] = int(_num(ses.get(src)) or 0)
        lv = [f"{anon(x.get('name'), 30)} {x.get('from')}→{x.get('to')}" for x in ses.get("levels") or []
              if isinstance(x, dict) and x.get("from") is not None]
        if lv:
            r["niveaux"] = lv[:5]
        g = ses.get("goal")
        if isinstance(g, dict) and g.get("text"):
            r["objectif"] = anon(g["text"])
        lb = [f"{anon(x.get('name'), 30)} +{int(_num(x.get('xp')) or 0)} XP" for x in ses.get("leaderboard") or []
              if isinstance(x, dict)]
        if lb:
            r["classement_xp"] = lb[:5]
        for key, src, n in (("butin", "loot", 4), ("evenements", "events", 6), ("quetes", "titles", 6)):
            v = texts(ses.get(src), n, 100)
            if v:
                r[key] = v
        if r:
            out["session"] = r
    return out or None


def character_context(db: Any) -> dict[str, Any]:
    """Compact, JSON-able facts about the current character from ``TreeAIDB`` ({} when unknown)."""
    try:
        c = current_character(db)
        if c is None:
            return {}
        ctx: dict[str, Any] = {}
        for key, names in (("classe", ("class", "classFR", "className")), ("race", ("race",)),
                           ("niveau", ("level", "lvl")), ("zone", ("zone", "zoneText", "realZone")),
                           ("sous_zone", ("subzone", "subZone"))):
            v = _pick(c, *names)
            if v is not None and not isinstance(v, (dict, list)):
                ctx[key] = v
        xp, xpmax = _num(_pick(c, "xp")), _num(_pick(c, "xpMax", "xp_max", "maxXP"))
        if xp is not None and xpmax:
            ctx["xp"] = f"{round(100 * xp / xpmax)} %"
        rest = _num(_pick(c, "rested", "restXP", "rest_xp"))
        if rest and xpmax:
            ctx["repos"] = f"{round(100 * rest / xpmax)} % d'un niveau"
        for key, fn in (("spe", _spec), ("sacs", _bags), ("familier", _pet)):
            v = fn(c)
            if v:
                ctx[key] = v
        gold = money_text(_pick(c, "money", "copper", "gold"))
        if gold:
            ctx["or"] = gold
        for key, names in (("munitions", ("ammo", "ammoCount")), ("durabilite", ("durability", "repair")),
                           ("metiers", ("professions", "profs"))):
            v = _pick(c, *names)
            if v is not None:
                ctx[key] = _short(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v, 120)
        db = db if isinstance(db, dict) else {}
        qs = _quests(c, db)
        if qs:
            ctx["quetes"] = qs
        grp = _group(db, c)
        if grp:
            ctx["groupe"] = grp
        sq = _squad(c)
        if sq:
            ctx["groupe_treeai"] = sq
        mi = _missions(c)
        if mi:  # « Hey coach, on en est où ? » : missions du groupe + session
            ctx["missions_groupe"] = mi
        try:  # hôtel des ventes (module Auction : summary.auction) → « quoi revendre ? »
            from treeaicoach.wow import auction

            hv = auction.auction_context(c)
            if hv:
                ctx["hotel_des_ventes"] = hv
        except Exception:
            log.debug("wow auction context failed", exc_info=True)
        try:  # équipement (module Gear : summary.gear) → « quoi améliorer ? »
            from treeaicoach.wow import gear

            eq = gear.gear_context(c)
            if eq:
                ctx["equipement"] = eq
        except Exception:
            log.debug("wow gear context failed", exc_info=True)
        return ctx
    except Exception:
        log.debug("character_context failed", exc_info=True)
        return {}


def load_plan(engine: Any = None) -> dict[str, Any] | None:
    """The route plan written by TreeAI (``Interface/AddOns/TreeAI_Plan/Plan.lua``), else None. Never raises."""
    plan = getattr(engine, "_wow_plan", None)
    if isinstance(plan, dict):
        return plan
    try:
        from treeaicoach.wow import optional  # noqa: PLC0415
        from treeaicoach.wow.planner import writer  # noqa: PLC0415

        mode, install = optional("mode"), optional("install")
        m = mode.current() if mode is not None else None
        inst = getattr(m, "install", None) if m is not None else None
        if inst is None and install is not None:
            path = ""
            try:
                from treeaicoach.config import load_config  # noqa: PLC0415

                path = str(getattr(load_config(), "wow_path", "") or "")
            except Exception:
                pass
            inst = install.find_wow(path)
        return writer.read_plan(inst.addons_dir) if inst is not None else None
    except Exception:
        log.debug("wow plan load failed", exc_info=True)
        return None


def plan_context(plan: Any, db: Any = None) -> list[str]:
    """Short lines about the chosen route (the one the addon says the player picked: ``chars[key].plan.route``,
    else the first one). [] when there is no plan for this character."""
    try:
        from treeaicoach.wow.planner import routes  # noqa: PLC0415

        if not isinstance(plan, dict):
            return []
        chosen = None
        c = current_character(db) if isinstance(db, dict) else None
        if isinstance(c, dict) and isinstance(c.get("plan"), dict):
            chosen = c["plan"].get("route")
        return [_short(x, 220) for x in routes.summary_lines(plan, chosen)][:4]
    except Exception:
        log.debug("wow plan context failed", exc_info=True)
        return []


def build_context(engine: Any = None, db: Any = None, facts: Iterable[str] | None = None) -> dict[str, Any]:
    """Everything the WoW prompt gets: character (SavedVariables) + live combat-log facts. Never raises."""
    try:
        db = db if db is not None else load_db(engine)
        ctx = character_context(db)
        trajet = plan_context(load_plan(engine), db) if ctx else []
        if trajet:  # right after the identity keys, so the 2500-char prompt cut never drops it
            head = {k: ctx[k] for k in ("classe", "race", "niveau", "zone", "sous_zone") if k in ctx}
            ctx = {**head, "trajet": trajet, **{k: v for k, v in ctx.items() if k not in head}}
        if ctx.get("classe") is not None and ctx.get("niveau") is not None:
            try:  # optional « données du jeu » (client tables, treeaicoach/wow/gamedata.py)
                from treeaicoach.wow import gamedata

                gd = gamedata.game_data_context(ctx["classe"], ctx["niveau"])
                if gd:
                    ctx["donnees_jeu"] = gd
            except Exception:
                log.debug("wow game data context failed", exc_info=True)
        if ctx.get("classe") is not None:
            try:  # talents (module Talents : summary.talents + parcours validés) → « quels talents ? »
                from treeaicoach.wow import talents

                c = current_character(db) or {}
                summ = c.get("summary") if isinstance(c.get("summary"), dict) else {}
                tal = talents.talents_context(ctx["classe"], ctx.get("niveau"), summ.get("talents") or c.get("talents"))
                if tal:  # right after the identity keys so the 2500-char prompt cut keeps it
                    head = {k: ctx[k] for k in ("classe", "race", "niveau", "zone", "sous_zone", "trajet") if k in ctx}
                    ctx = {**head, "talents": tal, **{k: v for k, v in ctx.items() if k not in head}}
            except Exception:
                log.debug("wow talents context failed", exc_info=True)
        f = list(facts) if facts is not None else live_facts(engine)
        if f:
            ctx["combat_recent"] = [_short(x, 140) for x in f][-_MAX_FACTS:]
        return ctx
    except Exception:
        log.debug("wow build_context failed", exc_info=True)
        return {}


def build_prompt(question: Any, ctx: dict[str, Any] | None = None, who: str = "") -> str:
    """User message: the question first, then the JSON state (or a note that it is unknown)."""
    q = _short(question, 500) or DEFAULT_QUESTION
    head = f"Question posée par {who} : {q}" if who else f"Question : {q}"
    if ctx:
        state = json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))[:2500]
        return f"{head}\nÉtat du personnage (JSON) : {state}"
    return head + "\n(État du personnage inconnu : addon TreeAI pas encore lu, fais /reload en jeu.)"


def wow_answer(provider: str, api_key: str, model: str, question: str, *, engine: Any = None,
               who: str = "", ctx: dict[str, Any] | None = None) -> str:
    """Blocking WoW-expert answer (counted in the daily request count). Raises AIError."""
    from treeaicoach import ai_advisor

    ai_advisor.DAILY_QUOTA.add(1)
    c = build_context(engine) if ctx is None else ctx
    return ai_advisor.call_llm(provider, api_key, model, WOW_PROMPT, build_prompt(question, c, who),
                               max_tokens=220)
