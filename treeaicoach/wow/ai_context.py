"""WoW-expert AI context: « Hey coach » / Discord questions asked while TreeAI is in WoW mode.

The answer must be short (1–3 spoken French sentences), correct, and grounded in the player's OWN live data, read
from the game's files only (Blizzard-allowed channels, no memory / screen / packet reading):

* ``TreeAIDB`` (the addon's SavedVariables, written at /reload or logout): quest log, in-game route / zone plan,
  group snapshot, AH deals, pet / ammo, dungeon, errors…  — its age is always known and said;
* ``Logs/WoWChatLog.txt`` (``/chatlog``), really live: XP, level-ups, zone (a private :class:`_ChatWatch`);
* the route plan the app wrote (``TreeAI_Plan``) and the combat-log coach's recent facts.

:func:`question_context` picks the sections the question needs (:func:`intents`), builds them from
:mod:`.ai_facts` (deterministic numbers: XP/h with its source and confidence, level ETA, the travel-vs-XP sum of a
detour, dungeon fit, AH « rien de sûr » reasons…), drops volatile data older than 15 min (the rest is labelled
with its age), replaces every player name by « Classe niveau », and keeps the JSON under :data:`STATE_MAX_CHARS`
(≈ :data:`TOKEN_BUDGET` tokens). :func:`answer_question` asks the LLM with :data:`WOW_PROMPT`; without AI (off,
quota, error) it says :func:`local_answer`, built from the same facts — never a made-up number.

Interface looked up LAZILY (other modules may be missing; every lookup has a fallback):
  - ``treeaicoach.wow.wow_mode_active() -> bool``        (else engine ``_wow_mode`` / ``wow_mode``, else False)
  - ``treeaicoach.wow.savedvars.load_treeai_db() -> dict | None``  (also tried: ``load_db``, ``read_db``,
    ``load_savedvars``; else engine ``_wow_db`` / ``wow_db``)
  - ``treeaicoach.wow.coach.recent_facts(limit) -> list[str]``      (also tried on ``treeaicoach.wow.combatlog``
    and on engine ``_wow_coach``)
  - ``treeaicoach.wow.mode.current().install`` (WowInstall: ``chat_log``, SavedVariables files)

Pure functions, never raise (``log.debug`` on errors) except :func:`wow_answer` (raises ``AIError``
like ``ai_advisor.general_answer``). The API key is only passed through to ``ai_advisor.call_llm``.
"""

from __future__ import annotations

import collections
import importlib
import json
import logging
import math
import re
import threading
import time
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

try:
    from treeaicoach.wow import ai_facts as F
except Exception:  # a patched build without the new module: the legacy context still answers
    log.debug("wow ai_facts unavailable", exc_info=True)
    F = None  # type: ignore[assignment]

#: System prompt: WoW Forever coach, French, short spoken answers grounded in the JSON, ToS-safe.
WOW_PROMPT = (
    "Tu es TreeAI, le coach vocal d'un joueur de World of Warcraft: Forever (client français de la famille "
    "Classic : niveau max 60, double spécialisation au niveau 40, points d'Héritage, camping, 9 classes "
    "retravaillées, quelques zones et donjons nouveaux). Il joue avec des amis (groupe de 2 à 4) et te pose une "
    "question à voix haute. Tu reçois en JSON l'état de SON jeu, tiré des fichiers du jeu (addon TreeAI, journal "
    "de discussion) et des calculs de TreeAI. Règles : "
    "1) Français, tutoiement, 1 à 3 phrases courtes faites pour être dites à voix haute : pas de liste, pas de "
    "markdown, pas d'emoji, pas de jargon (score, modèle). "
    "2) Commence par l'action conseillée, verbe d'abord, et donne UNE seule action claire, avec le pourquoi en "
    "quelques mots. "
    "3) N'utilise que les chiffres du JSON ou de la question : n'en invente aucun, n'en recalcule aucun. Si la "
    "donnée utile manque (absente ou listée dans « manque »), dis « je n'ai pas encore la donnée » et comment "
    "l'avoir en quelques mots. "
    "4) Une section avec « age » date du dernier /reload : si ça compte, dis-le. "
    "5) « Ça vaut le coup ? » : suis « calcul_detour » (verdict et chiffres). HV : ne conseille que les "
    "« affaires_sures » ; sinon dis qu'il n'y a rien de sûr et la raison principale. XP par heure : dis d'où "
    "elle vient si la confiance n'est pas bonne. "
    "6) Tes souvenirs de Classic (quêtes, zones, donjons, talents, familiers) peuvent être faux sur Forever : "
    "dis « d'après Classic » ou « à vérifier en jeu » quand tu t'en sers. "
    "7) Les membres du groupe sont désignés par classe et niveau : n'invente aucun nom. Si un ami pose la "
    "question, l'état reste celui du joueur. "
    "8) Jamais de bot, d'automatisation ni de logiciel interdit par Blizzard ; une macro lance une seule "
    "technique par touche. Ne parle pas de League of Legends sauf si on te le demande ; hors WoW, réponds "
    "simplement."
)

#: Hotkey « Demander à l'IA » without a spoken question, in WoW mode.
DEFAULT_QUESTION = "Quel est le conseil le plus utile pour moi maintenant ?"

#: The JSON state sent with a question stays under this many characters (≈ TOKEN_BUDGET tokens).
STATE_MAX_CHARS = 2800
TOKEN_BUDGET = 950
#: Answer length asked from the model (tokens) and what is read out loud.
ANSWER_MAX_TOKENS = 220
SPOKEN_SENTENCES, SPOKEN_MAX_CHARS = 3, 320
MSG_AI_OFF = "Conseil IA désactivé : choisis un fournisseur dans Réglages."
MSG_AI_DOWN = "L'IA ne répond pas pour l'instant."

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


# ------------------------------------------------------------------ live sources (chat log, install, plan)
class _ChatWatch:
    """The live chat log (``WoWChatLog.txt``) read for the AI only: a :class:`live.LiveHub` limited to that
    file (the WoW page has its own), polled when a question is asked — one ``stat``, new lines only. Keeps the
    parsed XP / level / zone events (with their time) so XP gained since the /reload can be counted."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hub: Any = None
        self._path = ""
        self._events: collections.deque = collections.deque(maxlen=4000)

    def _on_events(self, events: list) -> None:
        for ev in events or ():
            if getattr(ev, "kind", "") == "chat" and isinstance(getattr(ev, "data", None), list):
                self._events.extend(e for e in ev.data if isinstance(e, dict)
                                    and e.get("kind") in ("xp", "level", "zone", "death"))

    def read(self, path: Any) -> tuple[dict, float | None, list]:
        """(ChatSession dict, seconds since the file changed, events). Never raises."""
        p = str(path or "")
        if not p:
            return {}, None, []
        with self._lock:
            try:
                if self._hub is None or p != self._path:
                    from treeaicoach.wow import live  # noqa: PLC0415

                    self._hub, self._path = live.LiveHub(chat_log=p), p
                    self._events.clear()
                    self._hub.subscribe(self._on_events)
                self._hub.poll()
                snap = self._hub.snapshot()
                return dict(snap.get("chat") or {}), snap.get("chat_age"), list(self._events)
            except Exception:
                log.debug("chat log read failed", exc_info=True)
                return {}, None, []


_CHAT = _ChatWatch()


def _install() -> Any:
    """The WowInstall of the running WoW mode (None outside it). Never raises."""
    try:
        from treeaicoach.wow import optional  # noqa: PLC0415

        mode = optional("mode")
        m = mode.current() if mode is not None else None
        return getattr(m, "install", None) if m is not None else None
    except Exception:
        return None


def _savedvars_age(inst: Any, now: float) -> float | None:
    """Seconds since the TreeAI SavedVariables file was written (when the summary has no time)."""
    try:
        from treeaicoach.wow import optional  # noqa: PLC0415

        install = optional("install")
        files = install.savedvars_files(inst) if (install is not None and inst is not None) else []
        return max(0.0, now - files[0].stat().st_mtime) if files else None
    except Exception:
        return None


def live_state(engine: Any = None) -> dict:
    """{chat, chat_age, events} from the live chat log of the running WoW (empty outside WoW mode)."""
    inst = getattr(engine, "_wow_install", None) or _install()
    chat, age, events = _CHAT.read(getattr(inst, "chat_log", None) if inst is not None else None)
    return {"chat": chat, "chat_age": age, "events": events}


# ------------------------------------------------------------------ what the question is about
#: (intent, folded-text pattern), in precedence order: the first match is the main subject.
_INTENT_RULES: tuple[tuple[str, str], ...] = (
    ("detour", r"vaut (le|la) (coup|peine)|\brentable|interessant d'?aller|\bdetour\b"),
    ("dead", r"\bje suis mort|\bj'?suis mort|\bje (viens de )?mour|\bje meurs|\bje suis un fantome"),
    ("dungeon", r"donjon|\binstance|\binst\b|\bdj\b|\bdonj"),
    ("bestpet", r"(meilleur|quel|nouveau|autre|bon|changer de)\b.{0,25}\b(familier|pet|bete)|apprivois|\btame"),
    ("pet", r"familier|\bpet\b|\bbete\b|nourri|loyaute|dressage|munition|fleche|\bballes?\b|carquois|giberne|\bammo"),
    ("ah", r"\bhv\b|hotel des ventes|\benchere|\bvend|revend|\bachet|\baffaires?\b|\bflip|marchand|\bgris\b"),
    ("money", r"\bmon or\b|d'or\b|\bgold\b|\bpo\b|\bargent\b|\bthune|\briche\b"),
    ("errors", r"erreur|\bbug|bloqu|\bplant|marche pas|fonctionne pas|\bprobleme|\bsouci"),
    ("group", r"\bgroupe|\bpotes?\b|\bamis?\b|copains|\bequipe|\bteam\b|les autres|tout le monde|\bmorts?\b|"
              r"qui (a|est|n'|doit|fait|va)|ensemble|on en est"),
    ("next", r"on fait quoi|je fais quoi|quoi faire|\bensuite|prochaine?|par ou|\bou (je |on |j')?(vais|va|aller|irais)|"
             r"quelle zone|meilleure zone|\bzones?\b|\bmaintenant\b|\btrajet"),
    ("spells", r"\bsorts?\b|competence|\bmaitre|entraineur|apprendre|formation"),
    ("talents", r"talent|\bspe\b|specialisation|\bbuild\b"),
    ("gear", r"equipement|\bstuff\b|\barmes?\b|armure|amelior|upgrade|\bitems?\b|\bobjets?\b"),
    ("xp", r"\bxp\b|experience|\bniveau|\blevel|\blvl\b|\bmonte(r|s)?\b|\bup\b|combien de temps|par heure|\beta\b"),
    ("quests", r"\bquetes?\b|journal|\brendre|abandon"),
)

#: The sections each subject needs, most important first (the budget cuts from the end).
_SECTIONS: dict[str, tuple[str, ...]] = {
    "detour": ("joueur", "calcul_detour", "xp", "trajet", "quetes"),
    "dungeon": ("joueur", "donjon", "groupe"),
    "bestpet": ("joueur", "meilleur_familier", "familier"),
    "pet": ("joueur", "familier"),
    "ah": ("joueur", "hv"),
    "money": ("joueur", "hv"),
    "errors": ("joueur", "erreurs"),
    "group": ("joueur", "groupe", "trajet"),
    "dead": ("joueur", "trajet", "quetes", "alertes"),
    "next": ("joueur", "trajet", "quetes", "xp", "alertes"),
    "spells": ("joueur", "sorts"),
    "talents": ("joueur", "talents"),
    "gear": ("joueur", "equipement"),
    "xp": ("joueur", "xp", "quetes", "trajet"),
    "quests": ("joueur", "quetes", "trajet"),
    "default": ("joueur", "alertes", "trajet", "xp", "quetes", "groupe"),
}


def intents(question: Any) -> list[str]:
    """The subjects of a question, main one first (``["default"]`` when nothing matches)."""
    if F is None:
        return ["default"]
    q = F.fold(question)
    if not q.strip() or q.strip(" ?!.") == F.fold(DEFAULT_QUESTION).strip(" ?!.") or re.search(
            r"conseil le plus utile|\bun conseil\b|quoi de neuf", q):
        return ["default"]
    out = [name for name, pat in _INTENT_RULES if re.search(pat, q)]
    if "dead" in out:                         # « je suis mort » is about me, not the group's deaths
        out = [x for x in out if x != "group"]
    try:
        dest = F.find_destination(str(question or ""))
        if dest is None:                      # « rentable » without a place: not a travel question
            out = [x for x in out if x != "detour"]
        elif re.search(r"\baller\b|y aller|\bpasser|\bfiler", q) and "detour" not in out:
            out.insert(0, "detour")
        if "dungeon" not in out and F.asked_dungeon(str(question or "")) is not None:
            out.insert(0, "dungeon")
    except Exception:
        log.debug("intent lookups failed", exc_info=True)
    out = list(dict.fromkeys(out))
    return out or ["default"]


def alerts(snap: F.Snap) -> F.Section:
    """What needs doing now, whatever the question (« le conseil le plus utile »): facts only."""
    out: list[str] = []
    ident = F.identity(snap).data
    if ident.get("reparation"):
        out.append("répare-toi : " + ident["reparation"])
    free = F._n(snap.s.get("bagsFree"))
    if free is not None and free <= 2:
        out.append(f"sacs presque pleins ({int(free)} places)")
    p = F._d(snap.sub("hunter").get("pet"))
    if p.get("dead"):
        out.append("ton familier est mort")
    elif F._i(p.get("happiness")) == 1:
        out.append("ton familier est mécontent : nourris-le")
    a = F._d(snap.sub("hunter").get("ammo"))
    n = max(F._i(a.get("total")), F._i(a.get("equipped")))
    if a and n < 200:
        out.append(f"plus que {n} munitions")
    qs = snap.sub("quests")
    mx = F._i(F._d(snap.export.get("player")).get("logMax"))
    if mx and F._i(qs.get("count")) >= mx:
        out.append("journal de quêtes plein")
    tal = snap.sub("talents")
    if F._i(tal.get("unspent")):
        out.append(f"{F._i(tal.get('unspent'))} point(s) de talent à dépenser")
    lv = snap.sub("leveling")
    if F._i(lv.get("trainPending")) and F._i(lv.get("trainPending")) <= F.live_progress(snap)["level"]:
        out.append("nouveaux sorts à apprendre chez ton maître")
    return F.Section("alertes", out[:4], snap.age)


def _builders(snap: F.Snap, question: str) -> dict[str, Callable[[], Any]]:
    cache: dict[str, Any] = {}

    def xp() -> F.Section:
        if "xp" not in cache:
            cache["xp"] = F.xp_rate(snap)
        return cache["xp"]

    return {
        "joueur": lambda: F.identity(snap),
        "xp": xp,
        "quetes": lambda: F.quests(snap),
        "trajet": lambda: F.route_plan(snap),
        "groupe": lambda: F.group(snap),
        "hv": lambda: F.auction_house(snap),
        "donjon": lambda: F.dungeon(snap, question),
        "familier": lambda: F.pet(snap),
        "meilleur_familier": lambda: F.best_pet(snap),
        "erreurs": lambda: F.errors(snap),
        "calcul_detour": lambda: F.detour(snap, question, xp()),
        "talents": lambda: F.talents(snap),
        "equipement": lambda: F.gear(snap),
        "sorts": lambda: F.spells(snap),
        "alertes": lambda: alerts(snap),
    }


def approx_tokens(text: str) -> int:
    """Conservative token estimate for French JSON (≈ 3 characters per token)."""
    return int(math.ceil(len(text or "") / 3.0))


def _size(d: dict) -> int:
    return len(json.dumps(d, ensure_ascii=False, separators=(",", ":")))


def _labelled(data: Any, age: float) -> Any:
    """Old (non-volatile) data, kept with its age so the coach can say it."""
    tag = F.age_fr(age) + " (dernier /reload)"
    if isinstance(data, dict):
        return {"age": tag, **data}
    return {"age": tag, "liste": data}


def _shrink(data: Any) -> Any | None:
    """A smaller version of a section (longest list halved, then the last key dropped), None when empty."""
    if isinstance(data, list):
        return data[: len(data) // 2] or None
    if isinstance(data, dict):
        lists = [(len(json.dumps(v, ensure_ascii=False)), k) for k, v in data.items() if isinstance(v, list) and len(v) > 1]
        if lists:
            _n, k = max(lists)
            return {**data, k: data[k][: max(1, len(data[k]) // 2)]}
        keys = [k for k in data if k not in ("age",)]
        if len(keys) > 1:
            out = dict(data)
            out.pop(keys[-1])
            return out
    return None


#: What a dropped stale section was, for the « manque » list.
_STALE_NAMES = {"groupe": "état du groupe", "hv": "affaires de l'HV", "xp": "XP par heure", "trajet": "trajet",
                "quetes": "journal de quêtes", "familier": "familier", "joueur": "état du perso"}


def gather(engine: Any = None, *, db: Any = None, live: dict | None = None, plan: Any = None,
           facts: Iterable[Any] | None = None, now: float | None = None) -> F.Snap:
    """Everything known right now (explicit arguments win — tests / evals pass them)."""
    t = time.time() if now is None else float(now)
    if db is None:
        db = load_db(engine)
    if live is None:
        live = live_state(engine) if engine is not None or _install() is not None else {}
    if plan is None:
        plan = load_plan(engine) if engine is not None or _install() is not None else None
    f = list(facts) if facts is not None else live_facts(engine)
    sv_age = None
    if isinstance(db, dict):
        _k, _c, s = F.pick_char(db)
        if not F._n(s.get("updated")):
            sv_age = _savedvars_age(getattr(engine, "_wow_install", None) or _install(), t)
    return F.make_snap(db or {}, now=t, chat=live.get("chat"), chat_age=live.get("chat_age"),
                       events=live.get("events"), plan=plan, facts=f, sv_age=sv_age)


def question_context(question: Any, engine: Any = None, *, db: Any = None, live: dict | None = None,
                     plan: Any = None, facts: Iterable[Any] | None = None, now: float | None = None,
                     budget: int = STATE_MAX_CHARS) -> dict[str, Any]:
    """The JSON state for ONE question: the sections it needs, fresh or labelled, no names, within budget."""
    if F is None:
        return build_context(engine, db=db, facts=facts)
    try:
        snap = gather(engine, db=db, live=live, plan=plan, facts=facts, now=now)
        return assemble(snap, str(question or DEFAULT_QUESTION), budget=budget)
    except Exception:
        log.debug("wow question_context failed", exc_info=True)
        return {}


def assemble(snap: F.Snap, question: str, budget: int = STATE_MAX_CHARS) -> dict[str, Any]:
    if not snap.s:
        return {"fraicheur": F.freshness(snap),
                "manque": ["aucune donnée de l'addon TreeAI, connecte-toi en jeu puis tape /reload"]}
    want = intents(question)
    order: list[str] = []
    for w in want[:3]:
        for key in _SECTIONS.get(w, ()):
            if key not in order:
                order.append(key)
    if not order:
        order = list(_SECTIONS["default"])
    order = order[:7]
    builders = _builders(snap, question)
    out: dict[str, Any] = {"fraicheur": F.freshness(snap)}
    manque: list[str] = []
    sections: list[F.Section] = []
    for key in order:
        try:
            sec = builders[key]() if key in builders else None
        except Exception:
            log.debug("wow section %s failed", key, exc_info=True)
            sec = None
        if sec is not None:
            sections.append(sec)
    comb = F.combat(snap)
    if comb is not None:
        sections.append(comb)
    for sec in sections:
        data = sec.data
        if not data:
            continue
        if sec.age is not None and sec.age > F.STALE_S:
            if sec.volatile:
                manque.append(f"{_STALE_NAMES.get(sec.key, sec.key)} trop vieux ({F.age_fr(sec.age)}), fais /reload en jeu")
                continue
            data = _labelled(data, sec.age)
        data = F.anonymize_tree(data, snap.labels)
        while data is not None and _size({**out, sec.key: data}) > budget:
            data = _shrink(data)
        if data is not None:
            out[sec.key] = data
    if manque and _size({**out, "manque": manque}) <= budget:
        out["manque"] = manque
    return out


# ------------------------------------------------------------------ the answer without AI (same facts)
def _speak(text: str) -> str:
    """Text for the voice: « ≈ / ~ » said « environ », ranges « 22 à 30 », « 14 sur 20 », « (12) » for x12,
    « gain » for → +, « par heure / par jour », no « 35,0 »."""
    t = str(text or "")
    t = t.replace("≈ ", "environ ").replace("≈", "environ ").replace("~", "environ ")
    t = t.replace("XP/h", "XP par heure").replace("/jour", " par jour").replace("/h", " par heure")
    t = t.replace(" → +", ", gain ").replace("→", ":")
    t = re.sub(r"(\d)\s*–\s*(\d)", r"\1 à \2", t)
    t = re.sub(r"(\d+)/(\d+)", r"\1 sur \2", t)
    t = re.sub(r"\bx(\d+)\b", r"(\1)", t)
    t = re.sub(r"(\d),0\b", r"\1", t)
    return " ".join(t.split())


def _first(v: Any) -> str:
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v or "")


def _sentence(t: str) -> str:
    t = t.strip().rstrip(" ,;:")
    if not t:
        return ""
    t = t[0].upper() + t[1:]
    return t if t[-1] in ".!?…" else t + "."


def _unwrap(v: Any) -> Any:
    """A labelled list section ({"age", "liste"}) → the list."""
    if isinstance(v, dict) and "liste" in v and set(v) <= {"age", "liste"}:
        return v["liste"]
    return v


def local_answer(question: Any, ctx: dict[str, Any] | None) -> str:
    """1–2 spoken sentences from the facts alone (no AI). "" when the facts do not answer the question."""
    if F is None:
        return ""
    try:
        return _speak(_local_answer(str(question or DEFAULT_QUESTION), ctx or {}))
    except Exception:
        log.debug("wow local answer failed", exc_info=True)
        return ""


def _missing(sec: Any, ctx: dict) -> str:
    m = (sec.get("manque") if isinstance(sec, dict) else None) or ctx.get("manque")
    if m:
        return _sentence("Je n'ai pas encore la donnée : " + _first(m))
    return "Je n'ai pas encore la donnée : tape /reload en jeu."


def _local_answer(question: str, ctx: dict) -> str:
    want = intents(question)
    main = want[0]
    q = F.fold(question)
    if not ctx or list(ctx) in (["fraicheur"], ["fraicheur", "manque"]):
        return _missing({}, ctx) if main != "default" or ctx.get("manque") else ""
    j = ctx.get("joueur") or {}
    tr = ctx.get("trajet") or {}
    qs = ctx.get("quetes") or {}
    if main == "detour":
        d = ctx.get("calcul_detour") or {}
        nums = [x for x in (d.get("xp_des_quetes"), d.get("temps_total") and f"en {d['temps_total']}") if x]
        here = (d.get("xp_par_heure_ici") or d.get("xp_par_heure_repere") or "").split(" (")[0]
        facts = (_sentence(f"{d.get('destination', 'Là-bas')} : " + " ".join(nums)
                           + (f", soit {d['xp_par_heure_du_detour']}" if d.get("xp_par_heure_du_detour") else "")
                           + (f" contre {here} ici" if here and d.get("verdict") else ""))
                 if nums else "")
        if d.get("verdict"):
            return _sentence(d["verdict"]) + (" " + facts if facts else "")
        if facts:
            return facts + " " + _missing(d, ctx)
        return _missing(d, ctx)
    if main == "dungeon":
        d = ctx.get("donjon") or {}
        duo = next((k for k in d if re.fullmatch(r"a_\d", k)), None)
        if d.get("demande"):
            return _sentence(d["demande"])
        if duo:
            return _sentence(f"À {duo[2:]}, vise {_first(d[duo])}, d'après Classic")
        if d.get("a_ton_niveau"):
            return _sentence("À ton niveau : " + ", ".join(d["a_ton_niveau"][:2]) + " (niveau max d'après Classic)")
        return _missing(d, ctx)
    if main in ("ah", "money"):
        h = ctx.get("hv") or {}
        if main == "money" and j.get("or"):
            return _sentence(f"Tu as {j['or']}")
        if re.search(r"\bvend", q) and not re.search(r"achet|affaire", q):
            if h.get("a_vendre"):
                return _sentence("Vends " + _first(h["a_vendre"]))
            if h.get("objets_gris"):
                return _sentence(f"Vends tes objets gris au marchand : {h['objets_gris']}")
        if h.get("affaires_sures"):
            return _sentence("Affaire sûre : " + _first(h["affaires_sures"]))
        if h.get("rien_de_sur"):
            why = h["rien_de_sur"][1:]
            reason = " ".join(str(x) for x in why[:2]).rstrip(" :")
            return _sentence(f"Rien de sûr à l'HV pour l'instant{' : ' + reason if reason else ''}")
        if h.get("a_vendre"):
            return _sentence("Vends " + _first(h["a_vendre"]))
        return _missing(h, ctx)
    if main == "bestpet":
        b = ctx.get("meilleur_familier") or {}
        if b.get("a_apprivoiser"):
            return _sentence(f"Le plus intéressant près de ton niveau : {_first(b['a_apprivoiser'])} ; liste d'après "
                             "Classic, à vérifier en jeu")
        return _missing(b, ctx)
    if main == "pet":
        p = ctx.get("familier") or {}
        if re.search(r"munition|fleche|balle|ammo|carquois|giberne", q) and p.get("munitions"):
            return _sentence(f"Tu as {p['munitions']}")
        if str(p.get("familier", "")).startswith("TreeAI"):
            return _sentence(p["familier"])
        if p.get("familier"):
            out = _sentence(f"Ton familier : {p['familier']}")
            if p.get("munitions"):
                out += " " + _sentence(f"Munitions : {p['munitions']}")
            return out
        return _missing(p, ctx)
    if main == "errors":
        e = ctx.get("erreurs") or {}
        if e.get("etat"):
            return _sentence(e["etat"])
        parts = [x for x in (e.get("erreurs_lua") and f"erreurs Lua : {e['erreurs_lua']}", e.get("actions_bloquees")) if x]
        # the raw Lua message (« derniere_erreur ») is for the model and the file, never read out
        if parts:
            return _sentence(" ; ".join(parts)) + (" " + _sentence(e["que_faire"]) if e.get("que_faire") else "")
        return _missing(e, ctx)
    if main == "dead":
        step = tr.get("maintenant")
        return _sentence("Retourne à ton corps, ou ressuscite auprès du gardien s'il est loin") + (
            " " + _sentence(f"Ensuite : {step}") if step else "")
    if main == "group":
        g = ctx.get("groupe") or {}
        mem = _unwrap(g.get("membres")) if isinstance(g, dict) else None
        if re.search(r"degat|\bdps\b", q):
            return _sentence(g["degats"]) if isinstance(g, dict) and g.get("degats") else _sentence(
                "Je n'ai pas encore la donnée : TreeAI ne voit les dégâts du groupe qu'avec le compteur en donjon")
        if re.search(r"ensemble|on fait quoi", q) and isinstance(g, dict) and g.get("prochaine_mission"):
            return _sentence(g["prochaine_mission"])
        if mem:
            out = _sentence("Ton groupe : " + " ; ".join(mem[:3]))
            if isinstance(g, dict) and g.get("note"):
                out += " " + _sentence(g["note"])
            return out
        return _missing(g, ctx)
    if main == "xp":
        x = ctx.get("xp") or {}
        if x.get("niveau_max"):
            return _sentence(x["niveau_max"])
        if x.get("xp_par_heure"):
            eta_k = next((k for k in x if k.startswith("niveau_") and k.endswith("_dans")), None)
            sure = {"bonne": "fiable", "moyenne": "encore approximatif"}.get(str(x.get("confiance")), "à confirmer")
            out = f"Tu fais environ {x['xp_par_heure']} XP par heure ({x.get('source', '?')}, {sure})"
            if eta_k:
                out += f" : niveau {eta_k.split('_')[1]} dans {x[eta_k]}"
            out = _sentence(out)
            if x.get("astuce"):
                out += " " + _sentence(x["astuce"])
            return out
        return _missing(x, ctx)
    if main == "quests":
        if "abandon" in q and qs.get("a_abandonner"):
            return _sentence("Abandonne " + _first(qs["a_abandonner"]))
        if "rendre" in q:
            if qs.get("pretes_a_rendre"):
                n = len(qs["pretes_a_rendre"])
                return _sentence(f"Oui, {n} à rendre : " + ", ".join(qs["pretes_a_rendre"][:3]))
            return _sentence("Aucune quête prête à rendre au dernier /reload") if qs else _missing(qs, ctx)
        if "combien" in q and qs.get("journal"):
            return _sentence(f"Tu as {qs['journal']} quêtes dans ton journal")
        if tr.get("maintenant"):
            return _sentence(tr["maintenant"])
        return _missing(qs, ctx)
    if main in ("next", "default"):
        al = _unwrap(ctx.get("alertes")) or []
        out = ""
        if main == "default" and isinstance(al, list) and al:
            out = _sentence(str(al[0]))
        if re.search(r"zone", q) and tr.get("zones_de_ton_niveau"):
            return _sentence("Pour ton niveau : " + tr["zones_de_ton_niveau"])
        if tr.get("maintenant"):
            out = (out + " " if out else "") + _sentence(tr["maintenant"])
            if tr.get("pourquoi") and len(out) < 160:
                out += " " + _sentence(tr["pourquoi"])
            return out
        if qs.get("pretes_a_rendre"):
            return (out + " " if out else "") + _sentence("Rends " + _first(qs["pretes_a_rendre"]))
        return out or _missing(tr, ctx)
    sec_key = {"talents": "talents", "gear": "equipement", "spells": "sorts"}.get(main)
    sec = _unwrap(ctx.get(sec_key)) if sec_key else None
    if isinstance(sec, dict):
        for k, lead in (("prochain_point", "Prochain talent : "), ("prochains_solo", "Prochain talent : "),
                        ("ameliorations", "Amélioration : "), ("prochains_sorts", "Prochains sorts : "),
                        ("priorite_stats", "Priorité des stats : ")):
            if sec.get(k):
                return _sentence(lead + _first(sec[k]))
        return _missing(sec, ctx)
    return ""


# ------------------------------------------------------------------ one question, end to end
def answer_question(provider: str, api_key: str, model: str, question: Any, *, engine: Any = None,
                    who: str = "", ctx: dict[str, Any] | None = None) -> str:
    """The short text said for a « Hey coach » / Discord question in WoW mode. Never raises.

    AI configured and under quota: the WoW-expert answer on :func:`question_context`; otherwise (or when the AI
    fails) the deterministic :func:`local_answer` from the same facts, else the usual French notice."""
    from treeaicoach.voice_assistant import short_answer  # noqa: PLC0415

    q = str(question or "").strip() or DEFAULT_QUESTION
    c = ctx if ctx is not None else question_context(q, engine)
    prov = str(provider or "off").lower()
    ai_on = prov != "off" and (prov == "ollama" or bool(str(api_key or "").strip()))
    quota_left = True
    try:
        from treeaicoach import ai_advisor  # noqa: PLC0415

        quota_left = not bool(getattr(ai_advisor.DAILY_QUOTA, "exhausted", False))
    except Exception:
        pass
    if ai_on and quota_left:
        try:
            raw = wow_answer(provider, api_key, model, q, engine=engine, who=who, ctx=c)
            ans = short_answer(_speak(raw), sentences=SPOKEN_SENTENCES, max_chars=SPOKEN_MAX_CHARS)
            if ans.strip():
                return ans
        except Exception as exc:  # AIError, network…: the facts still answer
            log.info("IA (WoW) : %s", exc)
    local = local_answer(q, c)
    if local:
        return short_answer(local, sentences=SPOKEN_SENTENCES, max_chars=SPOKEN_MAX_CHARS)
    if not ai_on:
        return MSG_AI_OFF
    return MSG_AI_DOWN if quota_left else "Limite de questions IA atteinte pour aujourd'hui."


# ------------------------------------------------------------------ legacy context (kept: other modules' tests pin it)
# The « Hey coach » path uses question_context(); build_context()/character_context() stay for compatibility.
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
    """User message: the question first, then the JSON state (or a note that it is unknown). The JSON is never
    cut in the middle: trailing sections are dropped when it is too long."""
    q = _short(question, 500) or DEFAULT_QUESTION
    head = f"Question posée par {who} : {q}" if who else f"Question : {q}"
    if ctx:
        c = dict(ctx)
        state = json.dumps(c, ensure_ascii=False, separators=(",", ":"))
        while len(state) > 2 * STATE_MAX_CHARS and len(c) > 1:
            c.pop(list(c)[-1])
            state = json.dumps(c, ensure_ascii=False, separators=(",", ":"))
        return f"{head}\nÉtat du jeu (JSON) : {state}"
    return head + "\n(État du personnage inconnu : addon TreeAI pas encore lu, fais /reload en jeu.)"


def wow_answer(provider: str, api_key: str, model: str, question: str, *, engine: Any = None,
               who: str = "", ctx: dict[str, Any] | None = None) -> str:
    """Blocking WoW-expert answer (counted in the daily request count). Raises AIError."""
    from treeaicoach import ai_advisor

    ai_advisor.DAILY_QUOTA.add(1)
    c = question_context(question, engine) if ctx is None else ctx
    return ai_advisor.call_llm(provider, api_key, model, WOW_PROMPT, build_prompt(question, c, who),
                               max_tokens=ANSWER_MAX_TOKENS)
