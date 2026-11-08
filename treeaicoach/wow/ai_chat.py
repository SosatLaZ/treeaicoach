"""« Parler à l'IA » (4.1): the written chat with the coach, on its own app page (ui_page_aichat.py).

Same WoW context as « Hey coach » (:mod:`.ai_context`: the addon's SavedVariables, the live chat log, the app's
route plan, the combat-log facts; names replaced by « Classe niveau »), but for a text answer read on screen:

* :data:`CHAT_PROMPT` allows a longer, structured answer (a first sentence that answers, then short paragraphs or
  « - » bullets), French, tutoiement, numbers only from the JSON;
* the last :data:`HISTORY_TURNS` exchanges go into the prompt (« et après ? », « pourquoi ? » make sense);
* every answer carries :func:`freshness_line` (« Données lues il y a 3 min : fais /reload en jeu… »): the
  SavedVariables are only written at /reload or logout;
* a profession question gets the addon's « Métiers » block (not in the voice context).

:func:`ask` is blocking (worker thread) and never raises: it returns a :class:`Reply` (answer, or a friendly
French error with the deterministic facts when they say something). The API key is only passed to the provider
call; it is never logged, stored, or shown (:func:`scrub`). :func:`load_history` / :func:`save_history` keep the
conversation in ``ai_chat.json`` of the user data folder (role, text, time, freshness: nothing else)."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

log = logging.getLogger(__name__)

#: The suggestion chips under the conversation (the questions players ask most).
SUGGESTIONS: tuple[str, ...] = (
    "Où je vais maintenant ?",
    "Quelle quête prendre ?",
    "Rends mes quêtes au plus vite",
    "Comment monter mon métier ?",
    "Qu'est-ce que je donne à mes potes ?",
    "Meilleur familier à apprivoiser ici ?",
)

#: Exchanges (question + answer) kept in the prompt, and the length of each message there.
HISTORY_TURNS = 4
HISTORY_MSG_CHARS = 600
#: Messages kept on disk / on screen.
HISTORY_KEEP = 60
HISTORY_FILE = "ai_chat.json"
QUESTION_MAX_CHARS = 1000
#: The JSON state of a written answer may be larger than the spoken one.
CHAT_STATE_MAX_CHARS = 5000
ANSWER_MAX_TOKENS = 800
CHAT_TIMEOUT_S = 25.0

CHAT_PROMPT = (
    "Tu es TreeAI, le coach d'un joueur de World of Warcraft: Forever (client français de la famille Classic : "
    "niveau max 60, double spécialisation au niveau 40, points d'Héritage, camping, 9 classes retravaillées, "
    "quelques zones et donjons nouveaux). Il joue avec des amis (groupe de 2 à 4) et t'écrit dans la fenêtre "
    "« Parler à l'IA » de l'appli TreeAI : il lit ta réponse à l'écran, entre deux combats. Tu reçois en JSON "
    "l'état de SON jeu, tiré des fichiers du jeu (addon TreeAI, journal de discussion) et des calculs de TreeAI, "
    "et les derniers échanges. Règles : "
    "1) Français, tutoiement, ton simple et direct, pas de jargon (score, modèle, JSON). "
    "2) Première phrase = la réponse (verbe d'abord quand c'est un conseil). Ensuite, seulement si c'est utile : "
    "des paragraphes courts ou une liste à puces commençant par « - » (5 puces au plus), 150 mots au plus en "
    "tout. Pas de titre, pas de gras, pas de tableau, pas d'emoji. "
    "3) N'utilise que les chiffres du JSON, de la question ou de la conversation : n'en invente aucun, n'en "
    "recalcule aucun. Si la donnée utile manque (absente ou listée dans « manque »), dis « je n'ai pas encore la "
    "donnée » et comment l'avoir. "
    "4) « fraicheur » et les « age » disent quand l'addon a sauvegardé (au /reload ou à la déconnexion) : si ta "
    "réponse dépend d'une donnée vieille, dis-le et propose un /reload. "
    "5) « Ça vaut le coup ? » : suis « calcul_detour » (verdict et chiffres). HV : ne conseille que les "
    "« affaires_sures » ; sinon dis qu'il n'y a rien de sûr et la raison principale. XP par heure : dis d'où "
    "elle vient si la confiance n'est pas bonne. "
    "6) Tes souvenirs de Classic (quêtes, zones, donjons, talents, familiers, métiers) peuvent être faux sur "
    "Forever : dis « d'après Classic » ou « à vérifier en jeu » quand tu t'en sers. "
    "7) Les membres du groupe sont désignés par classe et niveau : n'invente aucun nom. "
    "8) Jamais de bot, d'automatisation ni de logiciel interdit par Blizzard ; une macro lance une seule "
    "technique par touche. Ne parle pas de League of Legends sauf si on te le demande ; hors WoW, réponds "
    "simplement. "
    "9) Les échanges précédents servent à comprendre la question (« et après ? », « pourquoi ? ») ; "
    "l'état du jeu en JSON est toujours le plus récent."
)

MSG_NO_PROVIDER = ("L'IA n'est pas configurée. Choisis un fournisseur dans Réglages > Conseils IA (Gemini, Groq et "
                   "OpenRouter ont une offre gratuite), colle ta clé, puis reviens ici.")
MSG_NO_KEY = "Il manque ta clé API. Colle-la dans Réglages > Conseils IA, puis reviens ici."
MSG_QUOTA = "Limite de questions IA atteinte pour aujourd'hui. Réessaie demain."
MSG_DOWN = "L'IA ne répond pas pour l'instant."
#: Friendly French lines for the provider errors (ai_advisor.AIError.code).
ERROR_LINES: dict[str, str] = {
    "key": "Ta clé API est refusée par le fournisseur. Vérifie-la dans Réglages > Conseils IA.",
    "nokey": MSG_NO_KEY,
    "quota": "Le quota gratuit du fournisseur est atteint. Réessaie dans une dizaine de minutes.",
    "rate": "Trop de questions en une minute. Attends quelques secondes et réessaie.",
    "offline": "Le fournisseur d'IA est injoignable (connexion coupée ?).",
    "model": "Le modèle choisi n'existe pas chez ce fournisseur. Vide le champ « Modèle » dans Réglages.",
    "server": "Le fournisseur d'IA a renvoyé une erreur.",
    "empty": "L'IA a renvoyé une réponse vide.",
    "bad": "La réponse de l'IA était illisible.",
}
OLLAMA_OFFLINE = "Ollama ne répond pas : lance « ollama serve » sur ce PC."

_METIER_RE = re.compile(r"metier|profession|artisanat|couture|tailleur|forge|alchimi|enchant|ingenier|cuisin|"
                        r"\bpeche|secouris|minage|mineur|herbori|depe[cç]|cuir|recette|compo(sant)?s?\b")


# ------------------------------------------------------------------ data
@dataclass
class Message:
    role: str                     # "me" (the player) or "coach"
    text: str
    t: float = field(default_factory=time.time)
    fresh: str = ""               # freshness line of a coach answer

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "text": self.text, "t": round(float(self.t), 1), "fresh": self.fresh}

    @classmethod
    def from_dict(cls, d: Any) -> "Message | None":
        if not isinstance(d, dict) or d.get("role") not in ("me", "coach"):
            return None
        text = str(d.get("text") or "").strip()
        if not text:
            return None
        try:
            t = float(d.get("t") or 0.0)
        except (TypeError, ValueError):
            t = 0.0
        return cls(str(d["role"]), text[:4000], t, str(d.get("fresh") or "")[:300])


@dataclass
class Reply:
    ok: bool
    text: str                     # the answer, or the friendly error line
    fresh: str = ""               # « Données lues il y a 3 min : fais /reload en jeu… »
    error: str = ""               # "" / "noai" / an AIError code / "error"
    local: str = ""               # on an error: what the facts alone say (may be empty)

    @property
    def retry(self) -> bool:
        """« Réessayer » makes sense (not for a missing provider / key)."""
        return not self.ok and self.error not in ("noai", "nokey", "key")


# ------------------------------------------------------------------ configuration (no import of ai_advisor here)
def ai_settings(cfg: Any) -> tuple[str, str, str]:
    """(provider, key, model) of the configuration (the key is only handed to the provider call)."""
    prov = str(getattr(cfg, "ai_provider", "off") or "off").strip().lower()
    key = str(getattr(cfg, "ai_api_key", "") or "").strip()
    model = str(getattr(cfg, "ai_model", "") or "").strip()
    return prov, key, model


def ai_status(cfg: Any) -> tuple[bool, str]:
    """(ready, French message when not): a provider is chosen and has its key (Ollama needs none). Pure, cheap:
    safe on the UI thread (ai_advisor is a large module, imported only by :func:`ask`)."""
    prov, key, _model = ai_settings(cfg)
    if prov in ("", "off"):
        return False, MSG_NO_PROVIDER
    if prov != "ollama" and not key:
        return False, MSG_NO_KEY
    return True, ""


def notice(cfg: Any) -> tuple[str, str] | None:
    """(title, how to fix) shown on the page while the AI cannot answer, else None."""
    ready, why = ai_status(cfg)
    if ready:
        return None
    if why == MSG_NO_PROVIDER:
        return "L'IA n'est pas configurée", why.split(". ", 1)[1]
    return "Il manque ta clé API", why.split(". ", 1)[1]


def scrub(text: Any, secret: str) -> str:
    """``text`` without the secret (an error text never shows the key, even if a library put it there)."""
    s = str(text if text is not None else "")
    sec = str(secret or "").strip()
    if len(sec) >= 4 and sec in s:
        s = s.replace(sec, "•••")
    return s


# ------------------------------------------------------------------ the WoW context
def _ac() -> Any:
    from treeaicoach.wow import ai_context  # noqa: PLC0415

    return ai_context


def _fold(text: Any) -> str:
    try:
        from treeaicoach.wow import ai_facts as F  # noqa: PLC0415

        return F.fold(text)
    except Exception:
        return str(text or "").lower()


def context_question(question: str, history: Sequence[Message] = ()) -> str:
    """The text the sections are picked from: a follow-up without a subject of its own (« et après ? ») borrows
    the subject of the previous question."""
    q = str(question or "").strip()
    try:
        if _ac().intents(q) != ["default"]:
            return q
    except Exception:
        return q
    prev = next((m.text for m in reversed(list(history)) if m.role == "me"), "")
    return f"{prev} {q}".strip() if prev else q


def freshness_line(age: Any, has_data: bool = True, live: bool = False) -> str:
    """One line under each answer: how old the game data is and how to refresh it."""
    if not has_data:
        return "Aucune donnée de jeu lue : connecte-toi en jeu puis tape /reload."
    try:
        a = None if age is None else max(0.0, float(age))
    except (TypeError, ValueError):
        a = None
    if a is None:
        line = "Âge des données inconnu : fais /reload en jeu pour les mettre à jour."
    elif a < 90:
        line = "Données lues à l'instant (dernier /reload)."
    else:
        from treeaicoach.wow.ai_facts import age_fr  # noqa: PLC0415

        line = f"Données lues {age_fr(a)} : fais /reload en jeu pour les mettre à jour."
    if live:
        line += " XP, niveau et zone en direct."
    return line


def _metiers(snap: Any) -> dict[str, Any] | None:
    """The addon's « Métiers » block (summary.prof): professions with their rank, and the plans it computed."""
    try:
        p = snap.sub("prof")
    except Exception:
        return None
    if not p:
        return None
    out: dict[str, Any] = {}
    profs = []
    for x in p.get("profs") or []:
        if isinstance(x, dict) and x.get("name"):
            profs.append({k: x.get(k) for k in ("name", "rank", "max") if x.get(k) is not None})
    if profs:
        out["metiers"] = profs
    if isinstance(p.get("plans"), (dict, list)) and p.get("plans"):
        out["plans"] = p["plans"]
    if isinstance(p.get("level"), (dict, list)) and p.get("level"):
        out["montee"] = p["level"]
    return out or None


def gather_context(question: str, history: Sequence[Message] = (), engine: Any = None, *, db: Any = None,
                   live: dict | None = None, plan: Any = None, facts: Iterable[Any] | None = None,
                   now: float | None = None, budget: int = CHAT_STATE_MAX_CHARS) -> tuple[dict[str, Any], str]:
    """(JSON state for the question, freshness line). File reads: call it on a worker thread. Never raises."""
    ac = _ac()
    q = context_question(question, history)
    try:
        if getattr(ac, "F", None) is None:
            ctx = ac.question_context(q, engine, db=db, live=live, plan=plan, facts=facts, now=now, budget=budget)
            return ctx, freshness_line(None, bool(ctx))
        snap = ac.gather(engine, db=db, live=live, plan=plan, facts=facts, now=now)
        ctx = ac.assemble(snap, q, budget=budget)
        if snap.s and _METIER_RE.search(_fold(q)):
            m = _metiers(snap)
            if m:
                m = ac.F.anonymize_tree(m, snap.labels)
                while m is not None and len(json.dumps({**ctx, "metiers": m}, ensure_ascii=False)) > budget + 1500:
                    m = ac._shrink(m)
                if m is not None:
                    ctx["metiers"] = m
        return ctx, freshness_line(snap.age, bool(snap.s), bool(snap.live))
    except Exception:
        log.debug("chat context failed", exc_info=True)
        return {}, freshness_line(None, False)


def data_freshness(engine: Any = None) -> str:
    """The freshness line alone (page header). File reads: worker thread. Never raises."""
    try:
        ac = _ac()
        snap = ac.gather(engine)
        return freshness_line(snap.age, bool(snap.s), bool(snap.live))
    except Exception:
        log.debug("chat freshness failed", exc_info=True)
        return freshness_line(None, False)


# ------------------------------------------------------------------ prompt and answer
def _clip(text: Any, limit: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def recent_exchanges(history: Sequence[Message], turns: int = HISTORY_TURNS) -> list[Message]:
    """The last ``turns`` question / answer pairs (a question left without answer is dropped)."""
    msgs = [m for m in history if m.role in ("me", "coach") and m.text.strip()]
    pairs: list[list[Message]] = []
    i = 0
    while i < len(msgs):
        if msgs[i].role == "me" and i + 1 < len(msgs) and msgs[i + 1].role == "coach":
            pairs.append([msgs[i], msgs[i + 1]])
            i += 2
        else:
            i += 1
    return [m for p in pairs[-max(0, int(turns)):] for m in p] if turns > 0 else []


def build_chat_prompt(question: str, ctx: dict[str, Any] | None, history: Sequence[Message] = (),
                      turns: int = HISTORY_TURNS) -> str:
    """User message: the recent exchanges (oldest first), then the question and the JSON state."""
    lines = []
    past = recent_exchanges(history, turns)
    if past:
        lines.append("Échanges précédents (du plus ancien au plus récent) :")
        for m in past:
            lines.append(("Joueur : " if m.role == "me" else "Coach : ") + _clip(m.text, HISTORY_MSG_CHARS))
        lines.append("")
    lines.append(_ac().build_prompt(_clip(question, QUESTION_MAX_CHARS), ctx or None))
    return "\n".join(lines)


def format_answer(text: Any) -> str:
    """The provider's text for the chat bubble: short paragraphs separated by a blank line, bullets « • »."""
    s = re.sub(r"<think>.*?(</think>|$)", " ", str(text or ""), flags=re.S | re.I)
    s = re.sub(r"\*\*|__|`+|^#+\s*", "", s, flags=re.M)
    out: list[str] = []
    prev_bullet = False
    for raw in s.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        m = re.match(r"^(?:[-•*·]|\d+[.)])\s+(.*)$", line)
        bullet = m is not None
        if bullet:
            line = "• " + m.group(1)
        if out and not (bullet and prev_bullet):
            out.append("")
        out.append(line)
        prev_bullet = bullet
    return "\n".join(out).strip()[:4000]


def error_line(code: str, provider: str = "") -> str:
    if code == "offline" and provider == "ollama":
        return OLLAMA_OFFLINE
    return ERROR_LINES.get(code, ERROR_LINES["server"])


def ask(cfg: Any, question: str, history: Sequence[Message] = (), engine: Any = None, *,
        caller: Callable[..., str] | None = None, db: Any = None, live: dict | None = None, plan: Any = None,
        facts: Iterable[Any] | None = None, now: float | None = None) -> Reply:
    """One written question, end to end (blocking: worker thread). Never raises; never logs the key."""
    q = str(question or "").strip()[:QUESTION_MAX_CHARS]
    ready, why = ai_status(cfg)
    if not ready:
        return Reply(False, why, error="noai" if why == MSG_NO_PROVIDER else "nokey")
    prov, key, model = ai_settings(cfg)
    ctx, fresh = gather_context(q, history, engine, db=db, live=live, plan=plan, facts=facts, now=now)
    try:
        from treeaicoach import ai_advisor  # noqa: PLC0415
    except Exception:
        log.info("Chat IA : module IA indisponible")
        return Reply(False, MSG_DOWN, fresh, error="error", local=_local(q, ctx))
    if bool(getattr(ai_advisor.DAILY_QUOTA, "exhausted", False)):
        return Reply(False, MSG_QUOTA, fresh, error="quota", local=_local(q, ctx))
    call = caller or ai_advisor.call_llm
    try:
        ai_advisor.DAILY_QUOTA.add(1)
        raw = call(prov, key, model, CHAT_PROMPT, build_chat_prompt(q, ctx, history),
                   timeout=CHAT_TIMEOUT_S, max_tokens=ANSWER_MAX_TOKENS, long=True)
        text = format_answer(scrub(raw, key))
        if not text:
            raise ai_advisor.AIError("empty")
        return Reply(True, text, fresh)
    except ai_advisor.AIError as exc:
        code = str(getattr(exc, "code", "") or "server")
        log.info("Chat IA : erreur %s (%s)", code, prov)
        return Reply(False, error_line(code, prov), fresh, error=code, local=_local(q, ctx))
    except Exception as exc:  # noqa: BLE001 - a broken provider client never takes the page down
        log.info("Chat IA : échec (%s)", type(exc).__name__)
        return Reply(False, MSG_DOWN, fresh, error="error", local=_local(q, ctx))


def _local(question: str, ctx: dict[str, Any]) -> str:
    """What the deterministic facts say without AI (same as the voice), or ""."""
    if not any(k not in ("fraicheur", "manque") for k in (ctx or {})):
        return ""                            # no game data: the freshness line already says how to get it
    try:
        text = str(_ac().local_answer(question, ctx) or "").strip()
    except Exception:
        return ""
    return text[:1].lower() + text[1:] if text[:2] != text[:2].upper() else text


# ------------------------------------------------------------------ history on disk (no secret)
def history_path() -> Path:
    from treeaicoach.paths import user_data_dir  # noqa: PLC0415

    return user_data_dir() / HISTORY_FILE


def load_history(path: Path | None = None) -> list[Message]:
    """The saved conversation (oldest first), [] when there is none or it is unreadable. Never raises."""
    try:
        p = path or history_path()
        if not p.is_file():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        items = data.get("messages") if isinstance(data, dict) else None
        msgs = [m for m in (Message.from_dict(d) for d in (items or [])) if m is not None]
        return msgs[-HISTORY_KEEP:]
    except Exception:
        log.debug("chat history unreadable", exc_info=True)
        return []


def save_history(messages: Sequence[Message], path: Path | None = None, secret: str = "") -> bool:
    """Write the conversation (role, text, time, freshness only; the key scrubbed if it ever got in). An empty
    conversation deletes the file. Never raises."""
    try:
        p = path or history_path()
        msgs = [m for m in messages if m.role in ("me", "coach")][-HISTORY_KEEP:]
        if not msgs:
            if p.is_file():
                p.unlink()
            return True
        rows = []
        for m in msgs:
            d = m.to_dict()
            d["text"], d["fresh"] = scrub(d["text"], secret), scrub(d["fresh"], secret)
            rows.append(d)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "messages": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception:
        log.debug("chat history not saved", exc_info=True)
        return False
