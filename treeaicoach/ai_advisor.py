"""Optional LLM advice ("Conseil IA"): a short French buy / macro tip at key moments.

Off by default (``cfg.ai_provider == "off"``). Supported providers (all have a free tier or
run locally):

* ``gemini``      Google AI Studio, REST ``models/{model}:generateContent`` (header ``x-goog-api-key``);
* ``groq``        OpenAI-compatible ``https://api.groq.com/openai/v1/chat/completions``;
* ``openrouter``  OpenAI-compatible ``https://openrouter.ai/api/v1/chat/completions`` (``:free`` models);
* ``ollama``      local ``http://127.0.0.1:11434/api/chat`` (no key);
* ``anthropic``   ``https://api.anthropic.com/v1/messages`` (``x-api-key`` + ``anthropic-version``).

Policy (:class:`AIAdvisor`): only at key moments (:class:`MomentDetector`: base visit with
gold, death, level 6 / 11 / 16, 60 s before the dragon / Baron, an enemy becoming fed), at most
one request every :data:`MIN_INTERVAL_S`, and a per-game budget (:class:`AIBudget`): at most
:data:`AUTO_BUDGET` automatic calls spread over priority slots (:data:`SLOTS`: first base with
gold, a lost fight / comeback moment, the mid-game turning point, pre-Baron / Elder, the late
game) plus exactly :data:`URGENT_BUDGET` bonus "urgence" call for a big problem
(:data:`URGENT_REASONS`). Manual requests (F8) are rate-limited and counted separately; one request at a time in a daemon thread with a
:data:`TIMEOUT_S` timeout (``urllib`` only). The game tick never waits: the engine polls
:meth:`AIAdvisor.poll` for a finished answer. Errors become one French status message
(:data:`ERROR_FR`) and a back-off; nothing here ever raises into the caller.

Plans (v2): the model must answer a strict JSON object (:data:`PLAN_SCHEMA_FR`, parsed and
validated by :func:`parse_plan`: ``plan`` + up to 3 ``etapes``); the snapshot carries the play
ratings of :mod:`treeaicoach.plays` (``coups``), the gold / level diffs (``diff``), the lane
priorities from the minion waves (``prio``), the next objective timers (``objt``), the enemy
jungler intel (``jgl``) and the recommended ward spots (``balises``), so plans are concrete
("le dragon dans 1:20, ta voie a la prio : rentre maintenant, pose 2 balises à..."). Key
moments also include a lost fight, the 80 s before a major objective, a gold swing
(:data:`SWING_GOLD` in 1-2.5 min) and the start of a comeback window. When the provider is
unreachable (offline, quota, back-off), :func:`rule_plan` builds the same kind of plan from the
same snapshot without any network ("PLAN" toast, no budget used).

Privacy: the snapshot sent holds champions, roles, levels, items, KDA, CS, gold and timers
only - never a summoner name / Riot ID.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import re
import socket
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from treeaicoach.fmtutil import clock

log = logging.getLogger(__name__)

TIMEOUT_S = 6.0
MIN_INTERVAL_S = 90.0
MIN_GAME_TIME_S = 90.0
MAX_RESPONSE_BYTES = 256 * 1024
MAX_OUTPUT_TOKENS = 200
PLAN_MAX_TOKENS = 320            # strict JSON plan (plan + 3 steps)
MAX_PLAN_CHARS = 170
MAX_STEP_CHARS = 95
MAX_STEPS = 3
RULES_MIN_INTERVAL_S = 90.0      # offline rule-based plans: at most one every 90 s
MAX_ADVICE_CHARS = 260
MAX_REVIEW_CHARS = 2500
REVIEW_TIMEOUT_S = 20.0
REVIEW_MAX_TOKENS = 700
MAX_ANALYSIS_BYTES = 12000
MANUAL_MIN_INTERVAL_S = 20.0
GROQ_MIN_TOKENS = 500
#: auto-pick order when the configured / default model does not exist for the key
MODEL_PREFERENCE: dict[str, tuple[str, ...]] = {
    "groq": ("openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b", "qwen/qwen3-32b",
             "meta-llama/llama-4-maverick-17b-128e-instruct", "llama-3.1-8b-instant"),
    "openrouter": ("meta-llama/llama-3.3-70b-instruct:free", "openai/gpt-oss-120b:free",
                   "deepseek/deepseek-chat-v3-0324:free", "google/gemini-2.0-flash-exp:free"),
}
_NOT_CHAT = ("whisper", "guard", "tts", "playai", "distil", "embed", "moderation", "allam", "compound")
_auto_model: dict[str, str] = {}    # "Demander à l'IA" (hotkey / button)
BASE_GOLD_MIN = 800             # "base visit with gold"
FED_LEVELS = (6, 11, 16)
OBJECTIVE_LEAD_S = 80.0
OBJECTIVE_WINDOW_S = 20.0       # announced once the remaining time is within 80 s +/- this (60..100 s)
OBJECTIVE_KEYS = ("dragon", "baron", "elder")
MAJOR_DRAGON_GT = 14 * 60.0     # a dragon is a "major" objective (budget slot) from 14:00
#: back-off after an error (seconds before the next automatic request)
BACKOFF_S: dict[str, float] = {"key": math.inf, "nokey": math.inf, "quota": 600.0, "offline": 300.0,
                               "model": math.inf, "server": 180.0, "empty": 90.0, "bad": 180.0}
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
#: per-game budget of automatic calls: one per priority slot (in priority order)
SLOTS = ("base", "objective", "comeback", "mid", "late")
AUTO_BUDGET = len(SLOTS)
URGENT_BUDGET = 1
#: hard ceiling of AI requests per game, all kinds together (automatic + urgence + manual).
#: Everything else is the rule-based engine (rule_plan), which costs nothing.
GAME_HARD_CAP = 10
URGENT_REASONS = frozenset({"gold", "death_streak", "teamfight"})
URGENT_MIN_INTERVAL_S = 30.0
MID_GAME_S = 14 * 60.0
LATE_GAME_S = 25 * 60.0


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    url: str
    default_model: str
    needs_key: bool
    key_url: str


PROVIDERS: dict[str, Provider] = {
    "gemini": Provider("gemini", "Google Gemini (gratuit)",
                       "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                       "gemini-2.0-flash", True, "https://aistudio.google.com/apikey"),
    "groq": Provider("groq", "Groq (gratuit)", "https://api.groq.com/openai/v1/chat/completions",
                     "openai/gpt-oss-120b", True, "https://console.groq.com/keys"),
    "openrouter": Provider("openrouter", "OpenRouter (modèles :free)",
                           "https://openrouter.ai/api/v1/chat/completions",
                           "meta-llama/llama-3.3-70b-instruct:free", True, "https://openrouter.ai/keys"),
    "ollama": Provider("ollama", "Ollama (local, sans clé)", "http://127.0.0.1:11434/api/chat",
                       "llama3.1", False, "https://ollama.com"),
    "anthropic": Provider("anthropic", "Anthropic Claude", "https://api.anthropic.com/v1/messages",
                          "claude-haiku-4-5-20251001", True, "https://console.anthropic.com/settings/keys"),
}
PROVIDER_CHOICES: tuple[tuple[str, str], ...] = (("off", "Désactivé"),) + tuple(
    (p.key, p.label) for p in PROVIDERS.values())

ERROR_FR: dict[str, str] = {
    "key": "Conseil IA : clé API invalide ou refusée.",
    "nokey": "Conseil IA : aucune clé API saisie.",
    "quota": "Conseil IA : quota gratuit atteint, nouvel essai dans 10 minutes.",
    "offline": "Conseil IA : service injoignable (hors ligne ?).",
    "model": "Conseil IA : modèle introuvable, vérifie son nom.",
    "server": "Conseil IA : le service a renvoyé une erreur.",
    "empty": "Conseil IA : réponse vide.",
    "bad": "Conseil IA : réponse illisible.",
}
OLLAMA_OFFLINE = "Conseil IA : Ollama ne répond pas (lance « ollama serve »)."

SYSTEM_PROMPT = (
    "Tu es un coach expert de League of Legends qui conseille un joueur pendant sa partie. "
    "Réponds en français, tutoiement, phrases très courtes, conseils concrets d'achat et de macro "
    "fondés sur les données (temps, lieux, objets exacts), pas de spéculation sur les temps de recharge "
    "ni les sorts d'invocateur ennemis. Pas de markdown, pas d'introduction. Quand un format JSON est "
    "demandé, réponds uniquement avec cet objet JSON."
)
MOMENT_FR = {
    "base": "retour en base avec de l'or à dépenser",
    "death": "je viens de mourir",
    "level": "je viens de passer un niveau clé",
    "objective": "objectif majeur dans environ 80 secondes",
    "fed": "un ennemi devient très fort (fed)",
    "test": "test de connexion",
    "manual": "le joueur demande un conseil maintenant",
}
ROLE_FR = {"TOP": "top", "JUNGLE": "jungle", "MIDDLE": "mid", "BOTTOM": "adc", "UTILITY": "support"}


class AIError(Exception):
    """A failed request; ``code`` is a key of :data:`ERROR_FR`."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def provider_spec(name: Any) -> Provider | None:
    return PROVIDERS.get(str(name or "").strip().lower())


def error_text(code: str, provider: str | None = None) -> str:
    if code == "offline" and provider == "ollama":
        return OLLAMA_OFFLINE
    return ERROR_FR.get(code, ERROR_FR["server"])


# ======================================================================================
# HTTP: request building / response parsing (pure) + the urllib call
# ======================================================================================
def build_request(provider: str, model: str, api_key: str, system: str, prompt: str,
                  url: str | None = None, max_tokens: int = MAX_OUTPUT_TOKENS,
                  json_mode: bool = False) -> tuple[str, dict[str, str], bytes]:
    """``(url, headers, json body)`` for one chat request. Raises :class:`AIError` ("nokey").

    ``json_mode``: ask the provider for a JSON object (Gemini ``responseMimeType``, OpenAI-style
    ``response_format``, Ollama ``format``; Anthropic follows the prompt)."""
    spec = provider_spec(provider)
    if spec is None:
        raise AIError("bad", f"unknown provider {provider!r}")
    model = (model or "").strip() or spec.default_model
    key = (api_key or "").strip()
    if spec.needs_key and not key:
        raise AIError("nokey")
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "TreeAICoach"}
    if provider == "gemini":
        target = (url or spec.url).replace("{model}", urllib.parse.quote(model, safe=""))
        headers["x-goog-api-key"] = key
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.4},
        }
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
    elif provider in ("groq", "openrouter"):
        target = url or spec.url
        headers["Authorization"] = f"Bearer {key}"
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/treeaicoach"
            headers["X-Title"] = "TreeAI Coach"
        body = {"model": model, "max_tokens": max_tokens, "temperature": 0.4,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        if provider == "groq":
            # reasoning models (gpt-oss, qwen3): without this the reasoning eats the tokens -> empty content
            body["max_tokens"] = max(max_tokens, GROQ_MIN_TOKENS)
            if "gpt-oss" in model:
                body["reasoning_effort"] = "low"
                body["include_reasoning"] = False
            elif "qwen3" in model:
                body["reasoning_format"] = "hidden"
        if json_mode:
            body["response_format"] = {"type": "json_object"}
    elif provider == "ollama":
        target = url or spec.url
        body = {"model": model, "stream": False, "options": {"num_predict": max_tokens, "temperature": 0.4},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        if json_mode:
            body["format"] = "json"
    else:  # anthropic
        target = url or spec.url
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
        body = {"model": model, "max_tokens": max_tokens, "system": system,
                "messages": [{"role": "user", "content": prompt}]}
    return target, headers, json.dumps(body, ensure_ascii=False).encode("utf-8")


def parse_response(provider: str, data: Any, long: bool = False, raw: bool = False) -> str:
    """Text of a provider's JSON answer (``raw``: only the reasoning removed, for a JSON plan).
    Raises :class:`AIError` ("empty" / "bad")."""
    try:
        if provider == "gemini":
            cands = data.get("candidates") or []
            parts = ((cands[0] or {}).get("content") or {}).get("parts") or [] if cands else []
            text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
        elif provider in ("groq", "openrouter"):
            choices = data.get("choices") or []
            text = str(((choices[0] or {}).get("message") or {}).get("content") or "") if choices else ""
        elif provider == "ollama":
            text = str((data.get("message") or {}).get("content") or "")
        else:
            text = "".join(str(b.get("text") or "") for b in data.get("content") or []
                           if isinstance(b, dict) and b.get("type", "text") == "text")
    except (AttributeError, TypeError, IndexError, KeyError) as exc:
        raise AIError("bad", str(exc)) from exc
    if raw:
        text = re.sub(r"<think>.*?</think>", " ", text, flags=re.S | re.I).strip()[:4000]
    else:
        text = clean_review(text) if long else clean_advice(text)
    if not text:
        raise AIError("empty")
    return text


def clean_advice(text: Any) -> str:
    """Plain text, no markdown, at most 2 sentences / :data:`MAX_ADVICE_CHARS` characters."""
    s = str(text or "")
    s = re.sub(r"<think>.*?</think>", " ", s, flags=re.S | re.I)    # reasoning models
    s = re.sub(r"[*_#`>]+", "", s)
    s = re.sub(r"^\s*[-•]\s*", "", s, flags=re.M)
    s = " ".join(s.split()).strip(" \"'«»")
    if not s:
        return ""
    sentences = re.findall(r"[^.!?]+[.!?]+|[^.!?]+$", s)
    s = " ".join(x.strip() for x in sentences[:2] if x.strip())
    if len(s) > MAX_ADVICE_CHARS:
        cut = s[:MAX_ADVICE_CHARS]
        s = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(",;: ") + "…"
    return s


def clean_review(text: Any) -> str:
    """Longer plain text (post-game review): no markdown emphasis, paragraphs kept, bounded."""
    s = re.sub(r"<think>.*?</think>", " ", str(text or ""), flags=re.S | re.I)
    s = re.sub(r"[*_#`]+", "", s)
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n|\n(?=\s*[-•\d])", s)]
    s = "\n".join(p for p in paras if p).strip()
    return s[:MAX_REVIEW_CHARS]


def _classify_http(code: int, body: str) -> str:
    low = body.lower()
    if code in (401, 403) or (code == 400 and ("api key" in low or "api_key" in low or "apikey" in low)):
        return "key"
    if "model_not_found" in low or "does not exist" in low or "decommissioned" in low:
        return "model"
    if code == 429 or "quota" in low or "rate limit" in low:
        return "quota"
    if code == 404:
        return "model"
    return "server"


def _opener_for(url: str) -> urllib.request.OpenerDirector:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in LOOPBACK_HOSTS:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never proxy localhost
    return urllib.request.build_opener()


def list_models(provider: str, api_key: str, url: str | None = None, timeout: float = TIMEOUT_S) -> list[str]:
    """Model ids of an OpenAI-compatible provider (GET .../models). [] on any error."""
    spec = provider_spec(provider)
    if spec is None or provider not in ("groq", "openrouter"):
        return []
    target = (url or spec.url).rsplit("/chat/completions", 1)[0] + "/models"
    req = urllib.request.Request(target, headers={"Authorization": f"Bearer {api_key.strip()}",
                                                  "Accept": "application/json", "User-Agent": "TreeAICoach"})
    try:
        with _opener_for(target).open(req, timeout=timeout) as resp:
            data = json.loads(resp.read(MAX_RESPONSE_BYTES).decode("utf-8", "replace"))
        return [str(m.get("id")) for m in data.get("data") or [] if isinstance(m, dict) and m.get("id")]
    except Exception:
        log.debug("Model list unavailable", exc_info=True)
        return []


def pick_model(provider: str, ids: Iterable[str]) -> str | None:
    """Best chat model among ``ids`` (preference list, then any non-audio / non-guard model)."""
    ids = [i for i in ids if not any(w in i.lower() for w in _NOT_CHAT)]
    for pref in MODEL_PREFERENCE.get(provider, ()):
        if pref in ids:
            return pref
    if provider == "openrouter":
        ids = [i for i in ids if i.endswith(":free")]
    return ids[0] if ids else None


def call_llm(provider: str, api_key: str, model: str, system: str, prompt: str,
             timeout: float = TIMEOUT_S, url: str | None = None, max_tokens: int = MAX_OUTPUT_TOKENS,
             long: bool = False, json_mode: bool = False) -> str:
    """One blocking request; returns the cleaned advice. Raises :class:`AIError` only.

    OpenAI-compatible providers: when the model does not exist for this key, the available
    models are listed once and the best chat model is picked (and remembered) automatically."""
    model = (model or "").strip() or _auto_model.get(provider, "")
    try:
        return _call_once(provider, api_key, model, system, prompt, timeout, url, max_tokens, long, json_mode)
    except AIError as exc:
        if exc.code != "model" or provider not in ("groq", "openrouter"):
            raise
        best = pick_model(provider, list_models(provider, api_key, url, timeout))
        if not best or best == model:
            raise
        log.info("AI model %r unavailable, using %r", model or "default", best)
        _auto_model[provider] = best
        return _call_once(provider, api_key, best, system, prompt, timeout, url, max_tokens, long, json_mode)


def _call_once(provider: str, api_key: str, model: str, system: str, prompt: str, timeout: float,
               url: str | None, max_tokens: int, long: bool, json_mode: bool = False) -> str:
    target, headers, body = build_request(provider, model, api_key, system, prompt, url, max_tokens, json_mode)
    req = urllib.request.Request(target, data=body, headers=headers, method="POST")
    try:
        with _opener_for(target).open(req, timeout=timeout) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(4096).decode("utf-8", "replace")
        except Exception:
            detail = ""
        raise AIError(_classify_http(int(exc.code), detail), f"HTTP {exc.code}") from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
        raise AIError("offline", type(exc).__name__) from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AIError("bad", "response too large")
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise AIError("bad", "not JSON") from exc
    if not isinstance(data, dict):
        raise AIError("bad", "not an object")
    return parse_response(provider, data, long, raw=json_mode)


# ======================================================================================
# Strict JSON plan
# ======================================================================================
PLAN_GOALS = ("dragon", "baron", "heraut", "larves", "tour", "farm", "vision", "defense", "regroupe",
              "achat", "aucun")
PLAN_URGENCY = ("haute", "moyenne", "basse")
PLAN_SCHEMA_FR = ('{"plan": "1 phrase, 170 caractères max", "etapes": ["action concrète, 95 caractères max", '
                  '"... (0 à 3)"], "objectif": "' + "|".join(PLAN_GOALS) + '", "urgence": "haute|moyenne|basse"}')


def _json_block(text: str) -> str | None:
    """The outermost ``{...}`` of ``text`` (code fences / reasoning removed), or None."""
    s = re.sub(r"<think>.*?</think>", " ", str(text or ""), flags=re.S | re.I)
    s = re.sub(r"```(?:json)?", " ", s, flags=re.I)
    i, j = s.find("{"), s.rfind("}")
    return s[i:j + 1] if 0 <= i < j else None


def _short(text: Any, limit: int) -> str:
    s = re.sub(r"[*_#`>]+", "", str(text or ""))
    s = " ".join(s.replace("\u2014", ":").split()).strip(" \"'«»")
    if len(s) > limit:
        cut = s[:limit]
        s = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(",;: ") + "…"
    return s


def parse_plan(text: Any) -> dict[str, Any] | None:
    """Strict parse of the model's JSON plan: ``{"plan", "etapes", "objectif", "urgence"}`` or None.

    ``plan`` must be a non-empty string; ``etapes`` a list of strings (at most :data:`MAX_STEPS`
    kept); unknown ``objectif`` / ``urgence`` values are dropped; extra keys ignored. Never raises."""
    try:
        block = _json_block(text)
        if block is None:
            return None
        data = json.loads(block)
        if not isinstance(data, dict):
            return None
        plan = data.get("plan")
        if not isinstance(plan, str) or len(plan.strip()) < 4:
            return None
        steps_raw = data.get("etapes", [])
        if steps_raw is None:
            steps_raw = []
        if not isinstance(steps_raw, list) or not all(isinstance(x, str) for x in steps_raw):
            return None
        steps = [_short(x, MAX_STEP_CHARS) for x in steps_raw if x.strip()][:MAX_STEPS]
        goal = str(data.get("objectif") or "").strip().lower()
        goal = unicodedata.normalize("NFKD", goal).encode("ascii", "ignore").decode()
        urg = str(data.get("urgence") or "").strip().lower()
        return {"plan": _short(plan, MAX_PLAN_CHARS), "etapes": steps,
                "objectif": goal if goal in PLAN_GOALS else None,
                "urgence": urg if urg in PLAN_URGENCY else None}
    except (ValueError, TypeError):
        return None


def plan_text(plan: dict[str, Any]) -> str:
    """One display line: the plan, then the steps (bounded like an advice)."""
    parts = [str(plan.get("plan") or "").strip()] + [str(x).strip() for x in plan.get("etapes") or []]
    parts = [p if p.endswith((".", "!", "?", "…")) else p + "." for p in parts if p]
    s = " ".join(parts)
    if len(s) > MAX_ADVICE_CHARS:
        cut = s[:MAX_ADVICE_CHARS]
        s = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(",;: ") + "…"
    return s


# ======================================================================================
# Game snapshot + prompt
# ======================================================================================
#: Abbreviated keys of the snapshot (sent once in the system prompt).
LEGEND = ("Clés JSON : t=temps de jeu, mo=moment, me=moi, al=alliés, en=ennemis, c=champion, r=rôle, "
          "lv=niveau, it=objets, ig=valeur des objets (or), g=or disponible, k=K/D/A, cs=sbires, "
          "ss=sorts d'invocateur, rs=réapparition dans (s), st=stats, ru=runes, ab=niveaux de sorts, "
          "ev=derniers événements, obj=objectifs (s avant apparition ou dispo), sb=tableau des scores "
          "(gd=écart d'or équipe estimé, kd=kills, ln=duels de voie), map=carte (z=zone, vu=vu il y a s), "
          "jgl=jungler ennemi, co=analyse du coach, wp=probabilité de victoire %, morts=mes dernières morts, "
          "achat=suggestion d'objet actuelle, ward=balise conseillée, coups=mes derniers coups notés "
          "(classe : brilliant, great, best, good, inaccuracy, mistake, blunder, miss + raison), prec=précision "
          "0-100, diff=écarts (eq=or d'équipe, po/niv/cs=contre mon adversaire de voie), prio=état des vagues "
          "par voie, objt=[objectif, s avant apparition, 0=dispo], balises=emplacements de balise conseillés, "
          "jint=infos sur la jungle ennemie.")
MAX_SNAPSHOT_BYTES = 6000
STAT_KEYS = {"attackDamage": "ad", "abilityPower": "ap", "armor": "ar", "magicResist": "mr",
             "attackSpeed": "as", "moveSpeed": "ms", "abilityHaste": "ah", "critChance": "crit",
             "lifeSteal": "vol", "physicalLethality": "leta", "magicPenetrationFlat": "penm"}
_DROP_KEYS = frozenset({"icon", "me_icon", "skin", "skin_id", "image", "frame", "minimap_bgr", "raw",
                        "series", "samples", "points", "path", "spots", "heatmap", "riot_id",
                        "summoner_name", "name_raw"})


def _clock(gt: Any) -> str:
    return clock(gt, "0:00", clamp=True)


def compact(value: Any, depth: int = 0, max_list: int = 10, max_str: int = 160) -> Any:
    """JSON-friendly, rounded, truncated copy of any value (dataclasses, enums, tuples...)."""
    if depth > 5:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return round(value, 2) if abs(value) < 10 else int(round(value))
    if isinstance(value, str):
        return value[:max_str]
    if hasattr(value, "value") and type(value).__module__ != "builtins" and isinstance(
            getattr(value, "value", None), (str, int)):          # Enum
        return compact(value.value, depth + 1)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = {f.name: getattr(value, f.name, None) for f in dataclasses.fields(value)
                 if not f.name.startswith("_")}
    elif not isinstance(value, (dict, list, tuple, set, frozenset)) and hasattr(value, "__dict__") \
            and type(value).__module__ != "numpy":
        value = {k: v for k, v in vars(value).items() if not k.startswith("_") and not callable(v)}
    if isinstance(value, dict):
        out = {}
        for k, v in list(value.items())[:40]:
            ks = str(k)
            if ks in _DROP_KEYS or ks.startswith("_"):
                continue
            cv = compact(v, depth + 1, max_list, max_str)
            if cv is None or cv == [] or cv == {} or cv == "":
                continue
            out[ks] = cv
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)[:max_list]
        return [c for c in (compact(v, depth + 1, max_list, max_str) for v in items) if c is not None]
    if type(value).__module__ == "numpy":
        try:
            return compact(value.item(), depth + 1) if getattr(value, "size", 2) == 1 else None
        except Exception:
            return None
    return None


def _item_list(items: Iterable[Any]) -> tuple[list[str], int]:
    try:
        from treeaicoach.scoreboard import item_info
    except Exception:  # pragma: no cover
        return [], 0
    names, gold = [], 0
    for it in items or ():
        info = item_info(it)
        if info is None:
            continue
        gold += int(info[1])
        if info[0] and info[2] not in ("trinket",):
            names.append(info[0])
    return names[:7], gold


def _role(p: Any, side: str, roles: Any) -> str | None:
    r = None
    try:
        if roles is not None and hasattr(roles, "role_of"):
            r = roles.role_of(p.champion_alias, side)
    except Exception:
        r = None
    r = r or getattr(p, "position", "") or None
    return ROLE_FR.get(str(r).upper(), None) if r else None


def _player(p: Any, side: str, roles: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"c": p.champion_name or p.champion_alias}
    role = _role(p, side, roles)
    if role:
        d["r"] = role
    names, gold = _item_list(p.items)
    d.update({"lv": int(p.level), "k": f"{p.kills}/{p.deaths}/{p.assists}", "cs": int(p.creep_score)})
    if names:
        d["it"] = names
    if gold:
        d["ig"] = gold
    spells = [str(s)[:20] for s in (getattr(p, "spells", ()) or ())][:2]
    if spells:
        d["ss"] = spells
    if getattr(p, "is_dead", False):
        d["rs"] = int(max(0.0, float(getattr(p, "respawn_timer", 0.0) or 0.0)))
    return d


def _names_to_champ(game: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        from treeaicoach.scoreboard import player_names

        for p in game.all_players():
            for n in player_names(p):
                out[n] = p.champion_name or p.champion_alias
    except Exception:
        pass
    return out


def _who(name: Any, lookup: dict[str, str]) -> str:
    s = str(name or "")
    if not s:
        return "?"
    c = lookup.get(s.casefold()) or lookup.get(s.split("#", 1)[0].casefold())
    if c:
        return c
    low = s.lower()
    if "turret" in low:
        return "tourelle"
    if "minion" in low:
        return "sbire"
    return "monstre" if ("sru_" in low or "baron" in low or "dragon" in low) else "?"


def _team_of(name: Any, game: Any) -> str | None:
    try:
        from treeaicoach.scoreboard import player_names

        s = str(name or "").casefold()
        for p in game.all_players():
            if s in player_names(p):
                return "nous" if p.team == game.my_team else "eux"
    except Exception:
        pass
    return None


def recent_events(game: Any, limit: int = 10) -> list[str]:
    """The last notable events as short French strings ("12:01 kill Zed>Ahri +Lee Sin")."""
    lookup = _names_to_champ(game)
    out: list[str] = []
    for ev in getattr(game, "events", None) or []:
        if not isinstance(ev, dict):
            continue
        name = ev.get("EventName")
        t = _clock(ev.get("EventTime"))
        if name == "ChampionKill":
            ast = [_who(a, lookup) for a in (ev.get("Assisters") or [])[:4]]
            out.append(f"{t} kill {_who(ev.get('KillerName'), lookup)}>{_who(ev.get('VictimName'), lookup)}"
                       + (f" +{','.join(ast)}" if ast else ""))
        elif name in ("DragonKill", "BaronKill", "HeraldKill", "HordeKill", "AtakhanKill"):
            kind = {"DragonKill": f"dragon {ev.get('DragonType') or ''}".strip(), "BaronKill": "baron",
                    "HeraldKill": "héraut", "HordeKill": "larves", "AtakhanKill": "atakhan"}[name]
            stolen = " volé" if str(ev.get("Stolen")).casefold() == "true" else ""
            out.append(f"{t} {kind}{stolen} pour {_team_of(ev.get('KillerName'), game) or '?'}")
        elif name in ("TurretKilled", "InhibKilled"):
            struct = str(ev.get("TurretKilled") or ev.get("InhibKilled") or "")
            mine = game.my_team
            owner = "ORDER" if "_T1_" in struct else "CHAOS" if "_T2_" in struct else None
            whose = "" if owner is None else (" (la nôtre)" if owner == mine else " (la leur)")
            out.append(f"{t} {'tour' if name == 'TurretKilled' else 'inhibiteur'} détruit{whose}")
        elif name in ("Ace", "Multikill", "FirstBlood"):
            if name == "Ace":
                out.append(f"{t} ace par {'nous' if ev.get('AcingTeam') == game.my_team else 'eux'}")
            elif name == "Multikill":
                out.append(f"{t} multikill x{ev.get('KillStreak')} {_who(ev.get('KillerName'), lookup)}")
            else:
                out.append(f"{t} premier sang {_who(ev.get('Recipient'), lookup)}")
    return out[-limit:]


def last_deaths(game: Any, limit: int = 3) -> list[dict[str, Any]]:
    """My last deaths: ``[{"t": "12:04", "par": "Darius", "aide": ["Lee Sin"]}]``."""
    me = getattr(game, "me", None)
    if me is None:
        return []
    from treeaicoach.scoreboard import player_names

    mine = player_names(me)
    lookup = _names_to_champ(game)
    out = []
    for ev in getattr(game, "events", None) or []:
        if not isinstance(ev, dict) or ev.get("EventName") != "ChampionKill":
            continue
        if str(ev.get("VictimName") or "").casefold() not in mine:
            continue
        d: dict[str, Any] = {"t": _clock(ev.get("EventTime")), "par": _who(ev.get("KillerName"), lookup)}
        ast = [_who(a, lookup) for a in (ev.get("Assisters") or [])[:4]]
        if ast:
            d["aide"] = ast
        out.append(d)
    return out[-limit:]


def engine_context(engine: Any, now: float | None = None) -> dict[str, Any]:
    """Live analysis of the engine (map state, coach, stance, fight, wards, win probability...).

    Every piece is optional (``getattr``-guarded): a missing analyser is simply left out. Never raises.
    """
    ctx: dict[str, Any] = {}
    if engine is None:
        return ctx
    try:
        if now is None:
            clock = getattr(engine, "_clock", None)
            now = float(clock()) if callable(clock) else time.monotonic()
    except Exception:
        now = time.monotonic()
    game = getattr(engine, "_game", None)
    my_team = getattr(game, "my_team", None)

    def safe(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception:
            return None

    tracker = getattr(engine, "_tracker", None)
    if tracker is not None:
        def tracks() -> dict[str, Any]:
            from treeaicoach import geometry

            def one(tr: Any) -> dict[str, Any] | None:
                pos = tr.position() if hasattr(tr, "position") else None
                if pos is None:
                    return None
                d = {"c": tr.alias or "?", "z": geometry.zone_label_fr(geometry.classify_zone(*pos), my_team)}
                ago = now - float(tr.last_seen)
                if ago > 1.5:
                    d["vu"] = int(ago)
                return d
            m: dict[str, Any] = {}
            me = tracker.me()
            if me is not None and me.position() is not None:
                m["moi"] = geometry.zone_label_fr(geometry.classify_zone(*me.position()), my_team)
            m["al"] = [x for x in (one(t) for t in tracker.allies(visible_only=False)[:4]) if x]
            m["en"] = [x for x in (one(t) for t in tracker.enemies(visible_only=False)[:5]) if x]
            return m
        ctx["map"] = safe(tracks)
    jt = getattr(engine, "jungler_status_text", None)
    if callable(jt):
        ctx["jgl"] = safe(jt)
    co: dict[str, Any] = {}
    coach = getattr(engine, "_coach", None)
    if coach is not None and hasattr(coach, "facts"):
        co["faits"] = safe(lambda: compact(coach.facts(), max_list=5, max_str=80))
        co["insight"] = safe(coach.insight) if hasattr(coach, "insight") else None
    stance = getattr(engine, "_stance", None)
    cur = safe(stance.current) if stance is not None and hasattr(stance, "current") else None
    if cur is not None:
        co["posture"] = f"{getattr(cur, 'level', '')} : {getattr(cur, 'reason', '')}"[:160]
    for attr in ("_fight", "_fight_tracker"):
        ft = getattr(engine, attr, None)
        if ft is not None and hasattr(ft, "state"):
            co["combat"] = safe(lambda ft=ft: compact(ft.state(), max_list=5, max_str=80))
            break
    for attr in ("_phase", "_map_state", "_endgame", "_macro"):
        ph = getattr(engine, attr, None)
        if ph is None:
            continue
        for meth in ("current", "state", "last"):
            if hasattr(ph, meth) and callable(getattr(ph, meth)):
                co["phase"] = safe(lambda ph=ph, meth=meth: compact(getattr(ph, meth)(), max_list=5, max_str=80))
                break
        break
    tip = getattr(engine, "_tip_text", None)
    if tip:
        co["astuce"] = str(tip)[:120]
    ctx["co"] = co
    for attr in ("_wards", "_ward_adv", "_ward_advisor"):
        wa = getattr(engine, attr, None)
        if wa is not None and hasattr(wa, "current"):
            ctx["ward"] = safe(lambda wa=wa: compact(wa.current(now), max_list=3, max_str=100))
            break
    wp = getattr(engine, "win_probability", None)
    p = safe(wp) if callable(wp) else None
    if isinstance(p, (int, float)):
        ctx["wp"] = int(round(100 * p))
    for key, fn in (("coups", _ctx_plays), ("diff", _ctx_diff), ("prio", _ctx_prio), ("balises", _ctx_wards),
                    ("jint", _ctx_jungle), ("genie", _ctx_genie)):
        val = safe(lambda fn=fn: fn(engine, game, now))
        if val not in (None, "", [], {}):
            ctx[key] = val
    pl = getattr(engine, "plays_summary", None)
    summ = safe(pl) if callable(pl) else None
    if isinstance(summ, dict) and summ.get("total"):
        ctx["prec"] = summ.get("precision")
    return compact(ctx) or {}


WAVE_FR = {"pushing": "prio (vague chez eux)", "pushed_in": "vague chez nous", "even": "équilibrée"}


def _ctx_genie(engine: Any, game: Any, now: float) -> dict[str, Any] | None:
    """The active rule-based macro call (macro.py, "COUP DE GÉNIE"): ``{"appel", "pourquoi"}``."""
    tac = getattr(engine, "_tactics", None)
    fn = getattr(tac, "macro_active", None)
    c = fn() if callable(fn) else None
    if c is None:
        return None
    return {"appel": str(c.text)[:110], "pourquoi": str(c.why)[:140]}


def _ctx_plays(engine: Any, game: Any, now: float) -> list[dict[str, Any]]:
    """My last rated moments (plays.py), most recent last: ``[{"c": "blunder", "r": "...", "t": "12:04"}]``."""
    pc = getattr(engine, "_plays", None)
    hist = pc.history() if pc is not None and hasattr(pc, "history") else []
    gt = float(getattr(game, "game_time", 0.0) or 0.0)
    out = [{"c": p.cls, "r": str(p.reason)[:90], "t": _clock(p.gt)} for p in hist if gt - float(p.gt) <= 600.0]
    return out[-6:]


def _ctx_diff(engine: Any, game: Any, now: float) -> dict[str, Any] | None:
    fn = getattr(engine, "scoreboard_summary", None)
    sb = fn() if callable(fn) else None
    if sb is None or not getattr(sb, "players", None):
        return None
    d: dict[str, Any] = {"eq": int(sb.team_gold_diff)}
    m = getattr(sb, "my_matchup", None)
    if m is not None:
        d.update(po=int(m.gold_diff), niv=int(m.level_diff), cs=int(m.cs_diff), vs=str(m.enemy))
    return d


def _ctx_prio(engine: Any, game: Any, now: float) -> dict[str, str] | None:
    coach = getattr(engine, "_coach", None)
    waves = coach.waves() if coach is not None and hasattr(coach, "waves") else {}
    out = {}
    for lane, w in (waves or {}).items():
        st = w.get("state") if isinstance(w, dict) else getattr(w, "state", None)
        if st in WAVE_FR:
            out[str(lane)] = WAVE_FR[st]
    return out or None


def _ctx_wards(engine: Any, game: Any, now: float) -> list[str] | None:
    """2 ward spots (wards.recommend) for the next objective / the enemy jungler's side."""
    from treeaicoach import geometry, wards

    me = getattr(game, "me", None)
    if me is None:
        return None
    tracker = getattr(engine, "_tracker", None)
    me_tr = tracker.me() if tracker is not None else None
    me_pos = me_tr.position() if me_tr is not None else None
    obj = None
    objs = getattr(engine, "_objectives", None)
    for o in sorted((objs.states() if objs is not None else []),
                    key=lambda o: 0.0 if getattr(o, "alive", False) else float(getattr(o, "remaining", None) or 1e9)):
        if getattr(o, "key", "") in ("dragon", "baron", "elder", "herald", "grubs"):
            rem = 0.0 if getattr(o, "alive", False) else float(getattr(o, "remaining", None) or 1e9)
            if rem <= 120.0:
                obj = (o.key, rem)
                break
    side = None
    jg = game.enemy_jungler() if hasattr(game, "enemy_jungler") else None
    if jg is not None and tracker is not None:
        tr = tracker.get(jg.champion_alias)
        pos = tr.position() if tr is not None else None
        if pos is not None and now - float(tr.last_seen) <= 60.0:
            side = geometry.side_of(*pos)
    roles = getattr(engine, "_role_resolver", None)
    role = roles.my_role() if roles is not None and hasattr(roles, "my_role") else None
    tac = getattr(engine, "_tactics", None)
    phase = (tac.phase() if tac is not None and hasattr(tac, "phase") else None) or "laning"
    picks = wards.recommend(game.my_team, role or me.position, phase=phase, me_pos=me_pos, objective=obj,
                            jungler_side=side, n=2)
    return [p.label for p in picks][:2] or None


def _ctx_jungle(engine: Any, game: Any, now: float) -> Any:
    """Enemy jungle intel from the detection modules when available (``engine.jungle_intel()``,
    ``engine._jungle_intel.summary()``...), compacted."""
    for name in ("jungle_intel", "jungler_intel"):
        fn = getattr(engine, name, None)
        if callable(fn):
            return compact(fn(), max_list=5, max_str=90)
    ji = getattr(engine, "_jungle_intel", None)
    st = ji.state() if ji is not None and hasattr(ji, "state") else None
    if st is not None and getattr(st, "alias", None):            # jungle_intel.JungleIntel (Tab data)
        d = {"c": getattr(st, "name", None) or st.alias, "txt": getattr(st, "text", None),
             "farm": getattr(st, "farm_side", None) if getattr(st, "farming", False) else None,
             "achat": bool(getattr(st, "recalled", False)) or None, "mort": bool(getattr(st, "dead", False)) or None,
             "lv": getattr(st, "level", None)}
        return {k: v for k, v in d.items() if v not in (None, "", 0)}
    for name in ("_jungle", "_game_settings"):
        obj = getattr(engine, name, None)
        for meth in ("summary", "state", "current"):
            fn = getattr(obj, meth, None) if obj is not None else None
            if callable(fn):
                return compact(fn(), max_list=5, max_str=90)
    return None


def _scoreboard(sb: Any) -> dict[str, Any] | None:
    if sb is None or not getattr(sb, "players", None):
        return None
    d: dict[str, Any] = {"gd": int(sb.team_gold_diff), "kd": f"{sb.ally_kills}-{sb.enemy_kills}"}
    try:
        d["ln"] = [m.text for m in sb.matchups][:5]
        if sb.fed:
            d["fed"] = list(sb.fed)[:3]
        if sb.spikes:
            d["pics"] = list(sb.spikes)[-3:]
    except Exception:
        pass
    return d


def candidate_items(game: Any, role: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
    """Items the AI may recommend (itemization.py): counters to the enemy profile + core items of
    my class, not owned, with French name, cost and why. [] when unknown. Never raises."""
    try:
        from treeaicoach import itemization as iz

        items = iz.load_items()
        me = getattr(game, "me", None)
        if me is None or not items:
            return []
        role = role or (getattr(me, "position", "") or None)
        cls = iz.champion_class(me.champion_alias, role)
        owned = {int(i) for i in me.items or ()}
        prof = iz.enemy_profile(getattr(game, "enemies", None) or (), items)

        def usable(iid: int) -> bool:
            it = items.get(iid)
            return it is not None and it.rift and iid not in owned and not any(
                iid in items[o].parts for o in owned if o in items)

        out: list[dict[str, Any]] = []
        seen: set[int] = set()
        for need, sev in sorted(prof.needs.items(), key=lambda kv: -kv[1]):
            if sev < iz.NEED_MIN or iz._owned_need(need, owned):
                continue
            names = prof.names.get(need) or []
            why = iz.REASONS[need].format(names=iz._join(names) if names else "Les ennemis")
            for iid in iz.NEED_ITEMS.get(need, {}).get(cls, ()):
                if usable(iid) and iid not in seen:
                    seen.add(iid)
                    out.append({"n": items[iid].name, "po": items[iid].gold, "pourquoi": why[:90]})
        for iid in iz.CORE.get(cls, ()):
            if usable(iid) and iid not in seen:
                seen.add(iid)
                out.append({"n": items[iid].name, "po": items[iid].gold, "pourquoi": "objet de base de ta classe"})
        return out[:limit]
    except Exception:
        log.debug("candidate items unavailable", exc_info=True)
        return []


def _norm_item(s: str) -> str:
    s = unicodedata.normalize("NFC", str(s)).replace("’", "'").casefold()
    return " ".join(s.split())


_BUY_RE = re.compile(
    r"(?i:ach[eè]te[rz]?|finis|finir|termine[rz]?|compl[eè]te[rz]?|prends|construis|rush|vise|ach[eè]vement|"
    r"objet|achat)\s*:?\s*(?i:(?:une?|la|le|les|des|ton|ta|tes|ensuite|d'abord|directement|tout de suite)\s+|l['’])*"
    r"([A-ZÉÈÀÂÎÔÛ][\w'’\-]*(?:\s+(?:[a-zéèêàâîôûç'’\-]+|[A-ZÉÈÀÂÎÔÛ][\w'’\-]*)){0,4})")


_NOT_ITEM_WORDS = frozenset({
    "baron", "nashor", "dragon", "drake", "drakes", "dragons", "héraut", "heraut", "larves", "atakhan", "elder",
    "ancestral", "tour", "tours", "inhibiteur", "nexus", "mid", "top", "bot", "jungle", "rivière", "flash",
    "téléportation", "embrasement", "ignite", "soin", "barrière", "fatigue", "purge", "fantôme", "châtiment",
    "vision", "contrôle", "balise", "ward", "wards", "objectifs", "objectif", "le", "la", "les", "un", "une",
    "ton", "ta", "tes", "ce", "cet", "cette", "ça", "puis", "et", "ou", "si", "en", "avant", "après",
})


def validate_item_advice(text: str, game: Any, candidates: Iterable[dict[str, Any]] = ()) -> bool:
    """False when the advice names an item that does not exist (hallucination) or tells me to buy
    an item I already finished. Heuristic on "achète / finis / rush X" phrases. Never raises."""
    try:
        from treeaicoach.scoreboard import item_table

        table = item_table()
        known = sorted({_norm_item(v[0]) for v in table.values() if v and v[0]}, key=len, reverse=True)
        if not known:
            return True
        owned_done = set()
        me = getattr(game, "me", None)
        for iid in (getattr(me, "items", None) or []) if me is not None else []:
            info = table.get(int(iid))
            if info is not None and info[2] in ("legendary", "boots"):
                owned_done.add(_norm_item(info[0]))
        allowed = {_norm_item(c.get("n", "")) for c in candidates or ()}
        for m in _BUY_RE.finditer(text):
            phrase = _norm_item(m.group(1))
            hit = next((k for k in known if phrase.startswith(k) or (len(phrase) >= 6 and k.startswith(phrase))),
                       None)
            if hit is None:
                first = phrase.split(" ", 1)[0]
                if first in _NOT_ITEM_WORDS:
                    continue
                words = {w for k in known for w in re.split(r"[\s'\-]+", k) if len(w) >= 4}
                if len(first) >= 4 and first.strip("'") in words:
                    continue                        # short form ("une Zhonya", "ta Rabadon")
                if any(_norm_item(c.champion_name or c.champion_alias) == first
                       for c in (game.all_players() if hasattr(game, "all_players") else [])):
                    continue                        # "prends Zed" (a champion), not an item
                log.info("AI advice rejected: unknown item %r", m.group(1))
                return False
            if hit in owned_done and hit not in allowed:
                log.info("AI advice rejected: %r already owned", hit)
                return False
        return True
    except Exception:
        log.debug("AI advice validation failed", exc_info=True)
        return True


def _fit(snap: dict[str, Any], limit: int) -> dict[str, Any]:
    """Drop the least useful details until the JSON fits in ``limit`` bytes."""
    def size() -> int:
        return len(json.dumps(snap, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    steps: list[Callable[[], None]] = [
        lambda: snap.get("co", {}).pop("faits", None),
        lambda: snap.__setitem__("ev", snap.get("ev", [])[-6:]) if "ev" in snap else None,
        lambda: [p.pop("ss", None) for p in snap.get("al", []) + snap.get("en", [])],
        lambda: snap.get("co", {}).pop("phase", None),
        lambda: snap.get("me", {}).pop("ru", None),
        lambda: [p.pop("it", None) for p in snap.get("al", [])],
        lambda: snap.pop("map", None),
        lambda: snap.pop("ev", None),
        lambda: snap.pop("co", None),
    ]
    for step in steps:
        if size() <= limit:
            break
        try:
            step()
        except Exception:
            pass
    return snap


def build_snapshot(game: Any, *, moment: str = "", roles: Any = None, scoreboard: Any = None,
                   objectives: Iterable[Any] = (), item_text: str | None = None,
                   context: dict[str, Any] | None = None, limit: int = MAX_SNAPSHOT_BYTES) -> dict[str, Any]:
    """Complete but compact game state for the prompt (no player names, < ``limit`` bytes). Never raises."""
    snap: dict[str, Any] = {}
    try:
        gt = float(getattr(game, "game_time", 0.0) or 0.0)
        snap["t"] = _clock(gt)
        if moment.startswith("comeback:"):
            reason = moment.split(":", 1)[1]
            snap["mo"] = COMEBACK_FR.get(reason, reason)
            snap["mode"] = "saisir" if reason in WINDOW_REASONS else "redresser"
        elif moment:
            snap["mo"] = MOMENT_FR.get(moment, moment)
        me = getattr(game, "me", None)
        if me is not None:
            mine = _player(me, "ally", None)
            try:
                r = roles.my_role() if roles is not None and hasattr(roles, "my_role") else None
            except Exception:
                r = None
            if r and ROLE_FR.get(str(r).upper()):
                mine["r"] = ROLE_FR[str(r).upper()]
            mine["g"] = int(getattr(game, "current_gold", 0.0) or 0)
            cs = getattr(game, "champion_stats", None) or {}
            if cs:
                st = {short: compact(float(cs[k])) for k, short in STAT_KEYS.items() if k in cs}
                if "currentHealth" in cs and "maxHealth" in cs:
                    st["pv"] = f"{int(cs['currentHealth'])}/{int(cs['maxHealth'])}"
                if "resourceMax" in cs and float(cs.get("resourceMax") or 0) > 0:
                    st["res"] = f"{int(cs.get('resourceValue', 0))}/{int(cs['resourceMax'])}"
                mine["st"] = {k: v for k, v in st.items() if v not in (None, 0)}
            info = getattr(game, "active_info", None) or {}
            if info.get("runes"):
                mine["ru"] = compact(info["runes"], max_list=9, max_str=40)
            if info.get("abilities"):
                mine["ab"] = info["abilities"]
            snap["me"] = mine
        snap["al"] = [_player(p, "ally", roles) for p in list(getattr(game, "allies", []))[:4]]
        snap["en"] = [_player(p, "enemy", roles) for p in list(getattr(game, "enemies", []))[:5]]
        sb = _scoreboard(scoreboard)
        if sb:
            snap["sb"] = sb
        objs = []
        for o in objectives or ():
            name = getattr(o, "name", "")
            if not name:
                continue
            if getattr(o, "alive", False):
                objs.append(f"{name}: dispo")
            elif getattr(o, "remaining", None) is not None and o.remaining < 900:
                objs.append(f"{name}: {int(o.remaining)} s")
        if objs:
            snap["obj"] = objs[:6]
        objt = []
        for o in objectives or ():
            name, rem = getattr(o, "name", ""), getattr(o, "remaining", None)
            if name and (getattr(o, "alive", False) or (rem is not None and rem < 900)):
                objt.append([name, 0 if getattr(o, "alive", False) else int(rem)])
        if objt:
            snap["objt"] = sorted(objt, key=lambda x: x[1])[:5]
        ev = recent_events(game)
        if ev:
            snap["ev"] = ev
        deaths = last_deaths(game)
        if deaths:
            snap["morts"] = deaths
        if item_text:
            snap["achat"] = str(item_text)[:160]
        try:
            api_role = roles.my_role() if roles is not None and hasattr(roles, "my_role") else None
        except Exception:
            api_role = None
        cands = candidate_items(game, api_role)
        if cands:
            snap["objets_possibles"] = cands
        for k, v in (context or {}).items():
            if v not in (None, "", [], {}):
                snap[k] = v
        snap = _fit(snap, limit)
    except Exception:
        log.debug("AI snapshot incomplete", exc_info=True)
    return snap


def build_prompt(snapshot: dict[str, Any]) -> str:
    moment = snapshot.get("mo") or "point de situation"
    data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    plan = ""
    if snapshot.get("mode") == "redresser":
        plan = ("Mode : redresser. Donne UN plan concret pour revenir dans la partie, fondé sur le JSON : où "
                "jouer, quoi éviter, quoi acheter et quel objectif échanger. ")
    elif snapshot.get("mode") == "saisir":
        plan = ("Mode : fenêtre favorable. Dis exactement quel objectif prendre maintenant et comment, "
                "fondé sur le JSON. ")
    return (f"Moment : {moment}.\nÉtat de la partie (JSON, API officielle + analyse de la minimap) : {data}\n{plan}"
            "Réponds UNIQUEMENT avec un objet JSON strict de cette forme : " + PLAN_SCHEMA_FR + ". "
            "Plan concret en 2 phrases courtes max au total : cite les temps (objt), la prio des voies (prio), "
            "les balises conseillées (balises), le jungler ennemi (jgl, jint) et mes coups récents (coups) quand "
            "c'est utile. Pas de spéculation sur les temps de recharge ennemis. Pour un achat, choisis UNIQUEMENT "
            "parmi objets_possibles (noms exacts) ou les composants de la suggestion « achat » ; ne conseille "
            "jamais un objet que j'ai déjà (me.it).")


# ======================================================================================
# Key moments
# ======================================================================================
class MomentDetector:
    """Detects the key moments worth an AI tip. ``update`` returns a moment id or None."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._dead: bool | None = None
        self._level: int | None = None
        self._in_base = False
        self._fed: set[str] | None = None
        self._obj_done: set[tuple] = set()
        self.last_objective = ""        # key of the objective behind the last "objective" moment

    def update(self, game: Any, in_base: bool = False, objectives: Iterable[Any] = ()) -> str | None:
        me = getattr(game, "me", None)
        if me is None:
            return None
        found: list[str] = []
        dead, level = bool(me.is_dead), int(me.level or 1)
        if self._dead is not None and dead and not self._dead:
            found.append("death")
        if self._level is not None and any(self._level < s <= level for s in FED_LEVELS):
            found.append("level")
        base_now = bool(in_base) and not dead
        if base_now and not self._in_base and float(getattr(game, "current_gold", 0) or 0) >= BASE_GOLD_MIN:
            found.append("base")
        try:
            from treeaicoach.itemization import is_fed

            fed = {str(p.champion_alias) for p in getattr(game, "enemies", []) if is_fed(p)}
        except Exception:
            fed = set()
        if self._fed is not None and fed - self._fed:
            found.append("fed")
        for o in objectives or ():
            key = getattr(o, "key", "") or ""
            rem = getattr(o, "remaining", None)
            if key not in OBJECTIVE_KEYS or getattr(o, "alive", False) or rem is None:
                continue
            sig = (key, round(float(getattr(o, "next_spawn", 0.0) or 0.0)))
            if abs(float(rem) - OBJECTIVE_LEAD_S) <= OBJECTIVE_WINDOW_S and sig not in self._obj_done:
                self._obj_done.add(sig)
                if "objective" not in found or key in ("baron", "elder"):
                    self.last_objective = key
                found.append("objective")
        self._dead, self._level, self._in_base, self._fed = dead, level, base_now, fed
        order = ("death", "base", "objective", "fed", "level")
        return next((m for m in order if m in found), None)


# ======================================================================================
# Advisor
# ======================================================================================
@dataclass(frozen=True)
class Advice:
    text: str
    moment: str
    t: float
    error: bool = False       # True: ``text`` is a French error message (manual request only)
    steps: tuple[str, ...] = ()   # the plan's steps (JSON plan), also folded into ``text``
    source: str = "ai"        # "ai" | "rules" (offline fallback, :func:`rule_plan`)
    goal: str | None = None   # PLAN_GOALS value of the plan, when given

    @property
    def title(self) -> str:
        """Toast title, showing why the AI (or the offline planner) spoke."""
        if self.error:
            return "IA"
        if self.moment == "manual":
            return "RÉPONSE IA"
        who = "PLAN" if self.source == "rules" else "IA"
        reason = self.moment.split(":", 1)[1] if self.moment.startswith("comeback:") else ""
        if reason:
            return f"{who} : Fenêtre à saisir" if reason in WINDOW_REASONS else f"{who} : Plan pour revenir"
        if self.moment == "objective":
            return f"{who} : Objectif"
        return "CONSEIL IA" if who == "IA" else "PLAN"


# ======================================================================================
# "Comeback" triggers: moments that can turn the game around
# ======================================================================================
COMEBACK_FR = {
    "gold": "l'écart d'or se creuse contre nous",
    "wp_drop": "la probabilité de victoire vient de chuter",
    "teamfight": "combat d'équipe perdu (plusieurs alliés morts)",
    "death_streak": "je meurs en série",
    "lane_fed": "mon adversaire de voie devient très fort",
    "objectives": "l'adversaire enchaîne les objectifs sans réponse",
    "carries_dead": "les carrys ennemis sont morts pour longtemps",
    "numbers": "Baron / Elder bientôt et nous sommes en surnombre",
    "ace": "ace : toute l'équipe adverse est morte",
    "swing": "l'écart d'or vient de basculer contre nous",
    "lead": "l'écart d'or vient de basculer pour nous",
    "blunders": "deux grosses erreurs coup sur coup (coups notés gaffe / erreur)",
}
WINDOW_REASONS = frozenset({"carries_dead", "numbers", "ace", "lead"})
SWING_GOLD = 1500               # team gold diff change ...
SWING_MIN_S, SWING_MAX_S = 60.0, 150.0   # ... over this time span
BLUNDER_WINDOW_S = 240.0
COMEBACK_COOLDOWN_S = 180.0
GOLD_STEP = 2000
WP_DROP = 0.15
WP_WINDOW_S = 180.0
_OBJ_EVENTS = ("DragonKill", "BaronKill", "HeraldKill", "HordeKill", "AtakhanKill", "TurretKilled", "InhibKilled")


class ComebackDetector:
    """Detects the situations where a concrete plan can turn the game around (or a window to seize).

    ``update`` returns a reason key of :data:`COMEBACK_FR` (each reason at most every
    :data:`COMEBACK_COOLDOWN_S`), or None. Pure, never raises."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._gold_tier = 0
        self._wp: list[tuple[float, float]] = []
        self._fed_seen: set[str] = set()
        self._last: dict[str, float] = {}
        self._seen_aces: set[Any] = set()
        self._gd: list[tuple[float, float]] = []

    def update(self, t: float, game: Any, scoreboard: Any = None, win_prob: float | None = None,
               objectives: Iterable[Any] = ()) -> str | None:
        try:
            return self._update(float(t), game, scoreboard, win_prob, list(objectives or ()))
        except Exception:
            log.debug("ComebackDetector failed", exc_info=True)
            return None

    def _update(self, t: float, game: Any, sb: Any, wp: float | None, objectives: list[Any]) -> str | None:
        me = getattr(game, "me", None)
        if me is None:
            return None
        gt = float(getattr(game, "game_time", 0.0) or 0.0)
        mine = me.team
        found: list[str] = []
        # gold deficit crossing -2k, -4k...
        if sb is not None and getattr(sb, "players", None):
            tier = int(max(0, -int(sb.team_gold_diff)) // GOLD_STEP)
            if tier > self._gold_tier:
                found.append("gold")
            self._gold_tier = tier if tier > self._gold_tier else min(self._gold_tier, tier + 1)
            m = getattr(sb, "my_matchup", None)
            fed = set(getattr(sb, "fed", ()) or ())
            opp = getattr(m, "enemy_alias", None) if m is not None else None
            if opp and opp in fed and opp not in self._fed_seen:
                found.append("lane_fed")
            self._fed_seen |= fed
            # gold swing: the team gold diff moved by SWING_GOLD within 1-2.5 minutes
            gd = float(sb.team_gold_diff)
            self._gd = [(ts, v) for ts, v in self._gd if t - ts <= SWING_MAX_S] + [(t, gd)]
            base = [v for ts, v in self._gd if t - ts >= SWING_MIN_S]
            if base and abs(gd - base[0]) >= SWING_GOLD:
                found.append("swing" if gd < base[0] else "lead")
                self._gd = [(t, gd)]
        # win probability drop
        if wp is not None:
            self._wp = [(ts, p) for ts, p in self._wp if t - ts <= WP_WINDOW_S] + [(t, float(wp))]
            if max(p for _ts, p in self._wp) - float(wp) > WP_DROP:
                found.append("wp_drop")
        # events: allied deaths, my deaths, objectives, ace
        from treeaicoach.hype import team_lookup

        lookup = team_lookup(game)
        my_names = {n for n, team in lookup.items() if team == mine}
        me_names = set()
        for attr in ("riot_id", "summoner_name"):
            v = str(getattr(me, attr, "") or "")
            if v:
                me_names |= {v.casefold(), v.split("#", 1)[0].casefold()}
        ally_deaths, my_deaths, ours, theirs, enemy_deaths = [], [], [], [], []
        for ev in getattr(game, "events", None) or []:
            if not isinstance(ev, dict):
                continue
            name = ev.get("EventName")
            et = float(ev.get("EventTime") or 0.0)
            if name == "ChampionKill":
                victim = str(ev.get("VictimName") or "").casefold()
                if victim in my_names:
                    ally_deaths.append(et)
                elif victim in lookup:
                    enemy_deaths.append(et)
                if victim in me_names:
                    my_deaths.append(et)
            elif name in _OBJ_EVENTS:
                if name in ("TurretKilled", "InhibKilled"):
                    struct = str(ev.get("TurretKilled") or ev.get("InhibKilled") or "")
                    owner = "ORDER" if "_T1_" in struct else "CHAOS" if "_T2_" in struct else None
                    if owner is not None:
                        (theirs if owner == mine else ours).append(et)
                else:
                    killer = str(ev.get("KillerName") or "").casefold()
                    team = lookup.get(killer)
                    if team is not None:
                        (ours if team == mine else theirs).append(et)
            elif name == "Ace" and ev.get("AcingTeam") == mine and gt - et <= 15.0:
                uid = (ev.get("EventID"), et)
                if uid not in self._seen_aces:
                    self._seen_aces.add(uid)
                    found.append("ace")
        recent = [x for x in ally_deaths if 0 <= gt - x <= 20.0]
        traded = [x for x in enemy_deaths if 0 <= gt - x <= 20.0]
        if len(recent) >= 2 and len(recent) > len(traded):          # a lost fight, not a trade
            found.append("teamfight")
        if len([x for x in my_deaths if 0 <= gt - x <= 240.0]) >= 2:
            found.append("death_streak")
        if (len([x for x in theirs if 0 <= gt - x <= 240.0]) >= 2
                and not [x for x in ours if 0 <= gt - x <= 240.0]):
            found.append("objectives")
        # windows to seize
        enemies = list(getattr(game, "enemies", []))
        allies = [me] + list(getattr(game, "allies", []))
        dead_long = [p for p in enemies if p.is_dead and float(p.respawn_timer or 0) >= 25.0]
        carries = [p for p in dead_long if p.position in ("BOTTOM", "MIDDLE") or p.kills >= 5]
        if len(dead_long) >= 2 and carries:
            found.append("carries_dead")
        soon = any(getattr(o, "key", "") in ("baron", "elder") and (getattr(o, "alive", False) or (
            getattr(o, "remaining", None) is not None and o.remaining <= 60)) for o in objectives)
        if soon and sum(p.is_dead for p in enemies) - sum(p.is_dead for p in allies) >= 2:
            found.append("numbers")
        order = ("ace", "numbers", "carries_dead", "teamfight", "wp_drop", "gold", "swing", "lead", "lane_fed",
                 "objectives", "death_streak")
        for r in order:
            if r in found and t - self._last.get(r, -math.inf) >= COMEBACK_COOLDOWN_S:
                self._last[r] = t
                return r
        return None


# ======================================================================================
# Offline planner (rule-based): same snapshot, no network
# ======================================================================================
PLAN_MOMENTS = frozenset({"death", "objective", "fed"})
_OBJ_LE = {"Dragon": "le dragon", "Baron": "le Baron", "Héraut": "le Héraut", "Larves": "les larves",
           "Dragon ancestral": "le dragon ancestral"}
_ROLE_LANE = {"top": "top", "mid": "mid", "adc": "bot", "support": "bot"}


def is_plan_moment(moment: str) -> bool:
    return moment in PLAN_MOMENTS or moment.startswith("comeback:")


def _mmss(sec: Any) -> str:
    return clock(sec, "0:00", clamp=True)


def _gold(n: Any) -> str:
    return f"{abs(int(n)):,}".replace(",", "\u202f") + " PO"


def rule_plan(moment: str, snap: dict[str, Any]) -> dict[str, Any] | None:
    """A concrete French plan built from the snapshot alone (offline fallback), in the
    :func:`parse_plan` format. None when there is nothing useful to say. Never raises."""
    try:
        return _rule_plan(str(moment or ""), snap if isinstance(snap, dict) else {})
    except Exception:
        log.debug("rule plan failed", exc_info=True)
        return None


def _rule_plan(moment: str, snap: dict[str, Any]) -> dict[str, Any] | None:
    reason = moment.split(":", 1)[1] if moment.startswith("comeback:") else ""
    genie = snap.get("genie")
    if isinstance(genie, dict) and genie.get("appel"):        # the macro planner's active call (macro.py)
        steps = [_short(genie.get("pourquoi"), MAX_STEP_CHARS)] if genie.get("pourquoi") else []
        return {"plan": str(genie["appel"]), "etapes": steps, "objectif": _goal(genie["appel"]),
                "urgence": "haute"}
    me = snap.get("me") or {}
    lane = _ROLE_LANE.get(str(me.get("r") or ""))
    prio = (snap.get("prio") or {}).get(lane or "", "")
    wards_ = [str(w) for w in (snap.get("balises") or [])][:2]
    objt = [o for o in (snap.get("objt") or []) if isinstance(o, list) and len(o) == 2]
    nxt = next((o for o in objt if int(o[1]) <= 120), None)
    diff = snap.get("diff") or {}
    gd = int(diff.get("eq", (snap.get("sb") or {}).get("gd", 0)) or 0)
    dead = [e for e in snap.get("en") or [] if isinstance(e, dict) and "rs" in e]
    jgl = str(snap.get("jgl") or "").strip()
    steps: list[str] = []
    # 1) a window: enemies dead for a while -> the biggest objective now
    long_dead = [e for e in dead if int(e.get("rs") or 0) >= 20]
    if reason in WINDOW_REASONS or len(long_dead) >= 2:
        if len(long_dead) < 1 and reason != "lead":
            return None
        names = ", ".join(str(e.get("c")) for e in long_dead[:3])
        rs = min((int(e.get("rs") or 0) for e in long_dead), default=0)
        up = [o for o in objt if int(o[1]) == 0]
        # V2 audit: Baron / Elder only with 3 dead, or 2 dead for 35 s+ (walk + kill); else the rest
        big_ok = len(long_dead) >= 3 or (len(long_dead) >= 2 and rs >= 35)
        target = (next((o[0] for o in up if o[0] in ("Baron", "Dragon ancestral")), None) if big_ok else None) or (
            next((o[0] for o in up if o[0] not in ("Baron", "Dragon ancestral")), None))
        what = _OBJ_LE.get(target, "une tour") if target else "une tour"
        if long_dead:
            plan = f"{len(long_dead)} ennemis morts ({names}) pendant {rs} s : prends {what} maintenant."
        else:
            plan = f"Avance de {_gold(gd)} : force {what} tant que l'écart est là."
        steps.append("Regroupe-toi avec ton équipe avant d'entrer.")
        if rs:
            steps.append(f"Repli dès qu'ils réapparaissent (dans {rs} s).")
        return {"plan": plan, "etapes": steps, "objectif": _goal(target) or "tour", "urgence": "haute"}
    # 2) a major objective soon: wave first, then vision
    if nxt is not None and (moment == "objective" or (not reason and moment not in ("death", "fed")
                                                      and int(nxt[1]) <= 100)):
        name, sec = nxt[0], int(nxt[1])
        when = "maintenant" if sec == 0 else f"dans {_mmss(sec)}"
        tail = ("ta voie a la prio, rentre maintenant et va te placer." if prio.startswith("prio")
                else "pousse ta vague d'abord, puis rentre te placer.")
        plan = f"{name} {when} : {tail}"
        if wards_:
            steps.append(f"Pose {len(wards_)} balise{'s' if len(wards_) > 1 else ''} : {', '.join(wards_)}.")
        if gd <= -2500:
            steps.append("Retard d'équipe : ne force pas, échange contre un objectif de l'autre côté.")
        elif sec > 0:
            steps.append("Sois sur place 20 s avant l'apparition.")
        return {"plan": plan, "etapes": steps[:MAX_STEPS], "objectif": _goal(name), "urgence": "moyenne"}
    # 3) behind / lost fight / deaths: play safe, concrete next step
    if reason or moment in ("death", "fed"):
        if reason == "teamfight":
            plan = "Combat perdu : défends tes tours sans te battre le temps que l'équipe réapparaisse."
        elif reason == "blunders":
            plan = "Deux grosses erreurs d'affilée : joue la sécurité et attends ton équipe."
        elif reason == "lane_fed" or moment == "fed":
            vs = str(diff.get("vs") or "ton adversaire")
            plan = f"{vs} est trop fort en duel : joue sous ta tour et farme sans échanger."
        elif gd <= -1000:
            plan = f"Retard de {_gold(gd)} : joue en sécurité près de tes tours et rattrape-toi au farm."
        else:
            plan = "Rejoue proprement : farme ta vague et attends ton jungler avant de te battre."
        if jgl:
            steps.append(_short(jgl, MAX_STEP_CHARS - 2).rstrip(".") + ".")
        if wards_:
            steps.append(f"Balise : {wards_[0]}.")
        if nxt is not None:
            steps.append(f"Prochain objectif : {nxt[0]} {('dispo' if int(nxt[1]) == 0 else 'dans ' + _mmss(nxt[1]))}.")
        return {"plan": plan, "etapes": steps[:MAX_STEPS], "objectif": "defense", "urgence": "moyenne"}
    return None


def _goal(name: Any) -> str | None:
    n = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    for g in ("baron", "larves"):
        if g in n:
            return g
    if "heraut" in n:
        return "heraut"
    if "dragon" in n:
        return "dragon"
    return None


class AIBudget:
    """Per-game budget of AI calls: :data:`AUTO_BUDGET` automatic calls (one per slot of
    :data:`SLOTS`), :data:`URGENT_BUDGET` bonus "urgence" call, manual calls counted apart."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.used: list[str] = []         # automatic slots consumed, in order
        self.urgent_used = 0
        self.manual = 0

    @property
    def auto_used(self) -> int:
        return len(self.used)

    @property
    def total(self) -> int:
        return self.auto_used + self.urgent_used + self.manual

    @property
    def exhausted(self) -> bool:
        return self.total >= GAME_HARD_CAP

    def pick(self, moment: str, game_time: float, objective: str = "") -> str | None:
        """The slot this moment would consume (``"urgent"`` for the bonus), or None (skip it)."""
        if self.exhausted:
            return None
        reason = moment.split(":", 1)[1] if moment.startswith("comeback:") else ""
        if reason in URGENT_REASONS and self.urgent_used < URGENT_BUDGET:
            return "urgent"
        if self.auto_used >= AUTO_BUDGET:
            return None
        cands: list[str] = []
        if moment == "base":
            cands.append("base")
        if moment == "objective" and (objective in ("baron", "elder")
                                      or (objective == "dragon" and game_time >= MAJOR_DRAGON_GT)):
            cands.append("objective")
        if moment == "death" or (reason and reason not in WINDOW_REASONS):
            cands.append("comeback")
        if MID_GAME_S <= game_time < LATE_GAME_S:
            cands.append("mid")
        elif game_time >= LATE_GAME_S:
            cands.append("late")
        return next((c for c in cands if c not in self.used), None)

    def take(self, slot: str) -> None:
        if slot == "urgent":
            self.urgent_used += 1
        elif slot not in self.used:
            self.used.append(slot)

    def refund(self, slot: str) -> None:
        if slot == "urgent":
            self.urgent_used = max(0, self.urgent_used - 1)
        elif slot in self.used:
            self.used.remove(slot)

    def snapshot(self) -> dict[str, Any]:
        return {"auto_used": self.auto_used, "auto_max": AUTO_BUDGET, "urgent_used": self.urgent_used,
                "urgent_max": URGENT_BUDGET, "manual": self.manual, "slots": list(self.used),
                "total": self.total, "cap": GAME_HARD_CAP}


def budget_text(b: dict[str, Any] | None) -> str:
    """Short French counter for the HUD / dashboard, e.g. ``"IA 3/5"`` (``+1`` once the bonus is used)."""
    if not b:
        return ""
    text = f"IA {int(b.get('auto_used', 0))}/{int(b.get('auto_max', AUTO_BUDGET))}"
    if b.get("urgent_used"):
        text += " +1"
    return text


class AIAdvisor:
    """Rate-limited background LLM advice. Thread-safe; public methods never raise."""

    def __init__(self, cfg: Any = None, *, clock: Callable[[], float] = time.monotonic,
                 caller: Callable[..., str] = call_llm, urls: dict[str, str] | None = None) -> None:
        self._clock = clock
        self._caller = caller
        self._urls = dict(urls or {})
        self._lock = threading.Lock()
        self._provider = "off"
        self._key = ""
        self._model = ""
        self._thread: threading.Thread | None = None
        self._result: Advice | None = None
        self._status: str | None = None
        self._status_seq = 0
        self._last_call = -math.inf
        self._blocked_until = -math.inf
        self.calls = 0
        self.budget = AIBudget()
        self.detector = MomentDetector()
        self.comeback = ComebackDetector()
        self._urgent_only = False
        self._bad_plays: list[float] = []
        self._last_rules = -math.inf
        self.rule_plans = 0                   # offline plans published (rules), this session
        self.json_errors = 0                  # answers that were broken JSON
        self._caller_json = _accepts(caller, "json_mode") and _accepts(caller, "max_tokens")
        self.apply_config(cfg)

    # ------------------------------------------------------------------ config / state
    def apply_config(self, cfg: Any) -> None:
        try:
            prov = str(getattr(cfg, "ai_provider", "off") or "off").lower()
            key = str(getattr(cfg, "ai_api_key", "") or "").strip()
            model = str(getattr(cfg, "ai_model", "") or "").strip()
            # Expert players: no automatic AI tip, only the bonus "urgence" call (and F8)
            self._urgent_only = str(getattr(cfg, "skill_level", "") or "").strip().lower() == "expert"
            with self._lock:
                if (prov, key, model) != (self._provider, self._key, self._model):
                    self._blocked_until = -math.inf     # new settings: retry right away
                    self._status = None
                self._provider, self._key, self._model = (prov if prov in PROVIDERS else "off"), key, model
        except Exception:
            log.exception("AIAdvisor.apply_config failed")

    @property
    def enabled(self) -> bool:
        return self._provider in PROVIDERS

    def reset(self) -> None:
        with self._lock:
            self._result = None
            self._last_call = -math.inf
            self._last_rules = -math.inf
            self._bad_plays = []
            self.budget.reset()
        self.detector.reset()
        self.comeback.reset()

    def status(self) -> tuple[int, str | None]:
        """``(sequence, French status)``: the sequence changes each time a new error is set."""
        with self._lock:
            return self._status_seq, self._status

    def budget_info(self) -> dict[str, Any]:
        """Counters of this game: ``auto_used / auto_max``, ``urgent_used / urgent_max``, ``manual``."""
        with self._lock:
            return self.budget.snapshot()

    def busy(self) -> bool:
        th = self._thread
        return th is not None and th.is_alive()

    # ------------------------------------------------------------------ live use
    def note_play(self, play: Any) -> None:
        """A rated moment (plays.py): two blunders / mistakes in :data:`BLUNDER_WINDOW_S` are a
        comeback moment ("comeback:blunders"). Never raises."""
        try:
            cls = str(getattr(play, "cls", ""))
            if cls in ("blunder", "mistake"):
                with self._lock:
                    self._bad_plays.append(float(getattr(play, "t", 0.0)))
                    del self._bad_plays[:-10]
        except Exception:
            log.debug("note_play failed", exc_info=True)

    def _blunder_moment(self, t: float) -> bool:
        with self._lock:
            recent = [x for x in self._bad_plays if 0.0 <= t - x <= BLUNDER_WINDOW_S]
        if len(recent) < 2 or t - self.comeback._last.get("blunders", -math.inf) < COMEBACK_COOLDOWN_S:
            return False
        self.comeback._last["blunders"] = t
        with self._lock:
            self._bad_plays = []
        return True

    def update(self, t: float, game: Any, *, in_base: bool = False, objectives: Iterable[Any] = (),
               roles: Any = None, scoreboard: Any = None, item_text: str | None = None,
               threat: int = 0, context: Any = None, win_prob: float | None = None,
               in_fight: bool = False) -> bool:
        """Detect a key moment and maybe start a request. Returns True if one was started.

        ``context``: extra snapshot sections (dict), or a callable returning them (only called
        when a request is really sent), e.g. ``lambda: engine_context(engine)``. When the provider
        is unreachable (back-off after an error), a plan moment gets an offline :func:`rule_plan`
        instead (``Advice.source == "rules"``, no budget used)."""
        try:
            objectives = list(objectives or ())
            moment = self.detector.update(game, in_base, objectives)
            reason = self.comeback.update(t, game, scoreboard, win_prob, objectives)
            if moment is None and reason is not None:
                moment = f"comeback:{reason}"
            if moment is None and self._blunder_moment(t):
                moment = "comeback:blunders"
            if moment is None or not self.enabled or threat >= 1 or (in_fight and moment.startswith("comeback")):
                return False
            gt = float(getattr(game, "game_time", 0.0) or 0.0)
            with self._lock:
                slot = self.budget.pick(moment, gt, self.detector.last_objective)
                if slot is None or (self._urgent_only and slot != "urgent"):
                    return False
                interval = URGENT_MIN_INTERVAL_S if slot == "urgent" else MIN_INTERVAL_S
                if gt < MIN_GAME_TIME_S or t - self._last_call < interval:
                    return False
                offline = t < self._blocked_until
                if not offline and self._thread is not None and self._thread.is_alive():
                    return False
                if not offline:
                    self._last_call = t
                    self.budget.take(slot)
            snap = build_snapshot(game, moment=moment, roles=roles, scoreboard=scoreboard,
                                  objectives=objectives, item_text=item_text, context=_resolve(context))
            fallback = rule_plan(moment, snap) if is_plan_moment(moment) else None
            if offline:
                self._offline_plan(fallback, moment, t)
                return False
            self._start(build_prompt(snap), moment, t, self._validator(game, snap, item_text), slot=slot,
                        fallback=fallback)
            return True
        except Exception:
            log.exception("AIAdvisor.update failed")
            return False

    def _offline_plan(self, plan: dict[str, Any] | None, moment: str, t: float) -> bool:
        """Publish an offline rule-based plan (rate-limited). True if one was set."""
        if not plan:
            return False
        with self._lock:
            if t - self._last_rules < RULES_MIN_INTERVAL_S or self._result is not None:
                return False
            self._last_rules = t
            self._result = Advice(plan_text(plan), moment, t, steps=tuple(plan.get("etapes") or ()),
                                  source="rules", goal=plan.get("objectif"))
            self.rule_plans += 1
        return True

    def ask(self, t: float, game: Any, *, roles: Any = None, scoreboard: Any = None,
            objectives: Iterable[Any] = (), item_text: str | None = None, context: Any = None) -> str:
        """Manual request ("Demander à l'IA"): returns a French acknowledgement for the user.

        The answer (or the error) arrives later through :meth:`poll` with ``moment == "manual"``."""
        try:
            if not self.enabled:
                return "Conseil IA désactivé : choisis un fournisseur dans Réglages > IA."
            if getattr(game, "me", None) is None:
                return "Pas de partie en cours : l'IA a besoin d'une partie pour conseiller."
            with self._lock:
                if self._thread is not None and self._thread.is_alive():
                    return "L'IA réfléchit déjà…"
                wait = MANUAL_MIN_INTERVAL_S - (t - self._last_call)
                if wait > 0:
                    return f"Patiente encore {int(math.ceil(wait))} s avant de redemander."
                if self._blocked_until == math.inf and self._status:
                    return self._status
                if self.budget.exhausted:
                    return f"Limite de {GAME_HARD_CAP} questions IA atteinte pour cette partie : le coach continue sans IA."
                self._last_call = t
                self.budget.manual += 1
            snap = build_snapshot(game, moment="manual", roles=roles, scoreboard=scoreboard,
                                  objectives=list(objectives or ()), item_text=item_text,
                                  context=_resolve(context))
            self._start(build_prompt(snap), "manual", t, self._validator(game, snap, item_text))
            return "Question envoyée à l'IA…"
        except Exception:
            log.exception("AIAdvisor.ask failed")
            return "Conseil IA indisponible."

    def poll(self) -> Advice | None:
        """The finished advice (once), else None."""
        with self._lock:
            res, self._result = self._result, None
            return res

    @staticmethod
    def _validator(game: Any, snap: dict[str, Any], item_text: str | None) -> Callable[[str], str | None]:
        cands = list(snap.get("objets_possibles") or [])

        def check(text: str) -> str | None:
            if validate_item_advice(text, game, cands):
                return text
            return str(item_text) if item_text else None      # hallucinated item: itemization fallback
        return check

    def _start(self, prompt: str, moment: str, t: float,
               validate: Callable[[str], str | None] | None = None, slot: str | None = None,
               fallback: dict[str, Any] | None = None) -> None:
        prov, key, model = self._provider, self._key, self._model
        url = self._urls.get(prov)
        kw: dict[str, Any] = {"timeout": TIMEOUT_S, "url": url}
        if _accepts(self._caller, "json_mode") and _accepts(self._caller, "max_tokens"):   # (may be swapped)
            kw.update(json_mode=True, max_tokens=PLAN_MAX_TOKENS)

        def job() -> None:
            try:
                raw = self._caller(prov, key, model, system_prompt(), prompt, **kw)
                plan = parse_plan(raw) if "{" in str(raw or "") else None
                steps: tuple[str, ...] = ()
                goal = None
                if plan is not None:
                    text: str | None = plan_text(plan)
                    steps, goal = tuple(plan["etapes"]), plan.get("objectif")
                elif "{" in str(raw or ""):
                    text = None                      # broken JSON: not shown as is
                    self.json_errors += 1
                else:
                    text = clean_advice(raw)         # a plain answer (model ignored the format)
                if text and validate is not None:
                    checked = validate(text)
                    if checked != text:
                        steps, goal = (), None
                    text = checked
                with self._lock:
                    self.calls += 1
                    if text:
                        self._result = Advice(text, moment, t, steps=steps, goal=goal)
                    elif moment == "manual":
                        self._result = Advice("L'IA n'a pas donné de conseil fiable cette fois.", moment, t,
                                              error=True)
                    elif fallback:
                        self._result = Advice(plan_text(fallback), moment, t, steps=tuple(fallback["etapes"]),
                                              source="rules", goal=fallback.get("objectif"))
                        self.rule_plans += 1
            except AIError as exc:
                self._fail(exc.code, prov, moment, t, slot, fallback)
            except Exception:
                log.exception("AI request failed")
                self._fail("server", prov, moment, t, slot, fallback)

        th = threading.Thread(target=job, name="TreeAICoach-ai", daemon=True)
        self._thread = th
        th.start()

    def _fail(self, code: str, provider: str, moment: str = "", t: float = 0.0,
              slot: str | None = None, fallback: dict[str, Any] | None = None) -> None:
        log.info("AI advice unavailable (%s, %s)", provider, code)
        with self._lock:
            if slot:
                self.budget.refund(slot)          # no answer: the slot stays available
            text = error_text(code, provider)
            if moment == "manual":
                self._result = Advice(text, moment, t, error=True)
            if text != self._status:
                self._status_seq += 1
            self._status = text
            self._blocked_until = self._clock() + BACKOFF_S.get(code, 180.0)
        if moment != "manual":
            self._offline_plan(fallback, moment, t)

    def wait(self, timeout: float = 10.0) -> bool:
        th = self._thread
        if th is not None:
            th.join(timeout)
        return not self.busy()


def _accepts(fn: Any, name: str) -> bool:
    """True when ``fn`` takes the keyword ``name`` (or ``**kwargs``)."""
    try:
        import inspect

        params = inspect.signature(fn).parameters
        return name in params or any(p.kind == p.VAR_KEYWORD for p in params.values())
    except (TypeError, ValueError):
        return False


def _resolve(context: Any) -> dict[str, Any] | None:
    try:
        ctx = context() if callable(context) else context
        return ctx if isinstance(ctx, dict) else None
    except Exception:
        log.debug("AI context unavailable", exc_info=True)
        return None


#: Rules of the live game the model may not know (its training data predates the 2026 season).
#: Sources: official patch notes 26.1 -> 26.19 (leagueoflegends.com), League of Legends wiki.
SEASON_RULES = (
    "Règles de la saison 2026 (patchs 26.x) : Atakhan n'existe plus (supprimé en 26.1, ne le mentionne "
    "jamais) ; larves du Néant à 8:00 (une seule fois, parties à 14:45), Héraut à 15:00, Baron à 20:00, "
    "dragons dès 5:00, âme au 4e dragon puis dragon ancestral ; premier sang +100 PO et première tour "
    "+300 PO ; les plaques de tour restent toute la partie (aussi sur les tours intérieures et "
    "d'inhibiteur), celles des tours extérieures valent moins de 11:00 à 15:00 ; quêtes de rôle : top = "
    "Téléportation gratuite (ou débridée), mid = bottes de niveau 3 et rappel en 4 s, bot = bottes dans un "
    "7e emplacement, support = balises de contrôle moins chères ; lampes féeriques : une balise posée "
    "dessus voit 25 % plus loin et révèle une zone 45 s."
)


def system_prompt() -> str:
    return f"{SYSTEM_PROMPT} {SEASON_RULES} {LEGEND}"


# ======================================================================================
# Post-game review
# ======================================================================================
REVIEW_PROMPT = (
    "Tu es un coach expert de League of Legends. Voici l'analyse (JSON) de la partie que le joueur vient "
    "de terminer. Écris UNIQUEMENT en français (tutoiement, pas de markdown, pas d'anglais) une revue "
    "courte et concrète, 7 phrases maximum : 2 points forts, puis 3 axes de progrès avec un exercice précis "
    "chacun, puis un conseil d'objets. Cite les chiffres exacts de l'analyse (morts, CS/min, écart d'or, "
    "avance des alertes...). Respecte le classement des morts de l'analyse (champ verdict : « 1v1 perdu », "
    "« alerte ignorée », « alerte trop tardive », « l'app n'a pas prévenu ») : ne reproche pas au joueur "
    "une alerte arrivée trop tard. OBJETS : nomme seulement des objets de la liste « objets autorisés », "
    "écrits exactement comme dans la liste (noms français) ; aucun autre objet, aucun nom anglais ; un objet "
    "légendaire ne s'achète jamais avant le premier retour en base. Pas de spéculation sur les temps de recharge."
)
#: an English review is rejected (the player is French): stop words counted per review
_EN_WORDS = re.compile(r"\b(the|and|you|your|with|should|before|after|item|build|first)\b", re.I)
REVIEW_MAX_EN_WORDS = 6


def compact_analysis(analysis: Any, limit: int = MAX_ANALYSIS_BYTES) -> dict[str, Any]:
    """The analysis.py result, compacted (rounded, heavy series dropped) to fit ``limit`` bytes."""
    a = compact(analysis if isinstance(analysis, dict) else {}, max_list=12, max_str=200) or {}
    for k in ("errors", "ok", "schema", "spoken_summary", "tip_items"):
        a.pop(k, None)

    def size() -> int:
        return len(json.dumps(a, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    for k in ("trends", "pathing", "exposure", "presence", "objective_presence", "zones", "phases",
              "alerts", "jungler", "ganks", "objectives", "scoreboard", "deaths"):
        if size() <= limit:
            break
        a.pop(k, None)
    return a


# ---------------------------------------------------------------- item grounding of the review
def _fold(text: Any) -> str:
    """Accent-free, lower-case, alphanumeric words separated by one space ("Sterak's Gage" ->
    "sterak gage", "Hydre titanesque" -> "hydre titanesque")."""
    s = unicodedata.normalize("NFKD", str(text or "").replace("’", "'"))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()
    s = re.sub(r"'s\b", "", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


_FR_COMMON = frozenset(_fold(w) for w in (
    "avant", "après", "premier", "première", "retour", "objets", "objet", "contre", "toujours", "pendant",
    "partie", "ennemis", "ennemi", "dégâts", "défense", "protection", "puissance", "vitesse", "soutien",
    "bouclier", "armure", "magique", "physique", "lumière", "dernier", "souffle", "gardien", "éternel",
    "mortel", "infini", "esprit", "nature", "chaîne", "rapide", "control", "contrôle", "warding", "health",
    "potion", "shield", "dragon", "guardian", "spirit", "mortal", "infinity", "sterling", "minion", "points",
    "longsword", "pickaxe", "refillable", "doran", "dorans",
))
_LEXICON: tuple[list[tuple[str, int]], dict[str, set[int]]] | None = None


def _item_lexicon() -> tuple[list[tuple[str, int]], dict[str, set[int]]]:
    """``(full names, distinctive single words)`` of the Rift items, French AND English (the AI
    sometimes answers with English names: "Titanic Hydra", "Sterak Gage"). Cached."""
    global _LEXICON
    if _LEXICON is not None:
        return _LEXICON
    full: list[tuple[str, int]] = []
    words: dict[str, set[int]] = {}
    try:
        from treeaicoach.paths import asset_path

        data = json.loads(asset_path("items.json").read_text(encoding="utf-8"))
        fr_words: set[str] = set()
        rows = []
        for k, v in (data.get("items") or {}).items():
            try:
                iid = int(k)
            except (TypeError, ValueError):
                continue
            if not v.get("p", 1):
                continue
            fr, en, kind = _fold(v.get("n")), _fold(v.get("en")), str(v.get("k") or "")
            rows.append((iid, fr, en, kind))
            fr_words.update(fr.split())
        for iid, fr, en, kind in rows:
            for name in {fr, en} - {""}:
                if len(name) >= 4:
                    full.append((name, iid))
            if kind not in ("legendary", "boots"):
                continue
            for name in {fr, en} - {""}:
                for w in name.split():
                    if len(w) >= 6 and w not in _FR_COMMON and not (name == en and w in fr_words and w not in fr.split()):
                        words.setdefault(w, set()).add(iid)
        full.sort(key=lambda x: -len(x[0]))
    except Exception:
        log.debug("item lexicon unavailable", exc_info=True)
    _LEXICON = (full, words)
    return _LEXICON


def items_mentioned(text: str) -> set[int]:
    """Item ids named in ``text`` (French or English, full name or a distinctive word)."""
    full, words = _item_lexicon()
    f = f" {_fold(text)} "
    out: set[int] = set()
    for name, iid in full:
        if f" {name} " in f:
            out.add(iid)
            f = f.replace(f" {name} ", " ")
    for w in f.split():
        ids = words.get(w)
        if ids:
            out.add(min(ids) if len(ids) == 1 else -min(ids))      # negative: ambiguous word ("hydra")
            if len(ids) > 1:
                _AMBIG[-min(ids)] = frozenset(ids)
    return out


_AMBIG: dict[int, frozenset[int]] = {}


def review_item_candidates(analysis: Any, limit: int = 12) -> tuple[list[dict[str, Any]], list[str]]:
    """``(allowed items, final build names)`` for the post-game review: my final build + the core
    items of my champion class + the counters to the enemy team (itemization.py), French names.
    Never raises."""
    try:
        from treeaicoach import itemization as iz

        items = iz.load_items()
        a = analysis if isinstance(analysis, dict) else {}
        s = a.get("summary") if isinstance(a.get("summary"), dict) else {}
        alias = str(s.get("champion") or "")
        role = str(s.get("position") or "") or None
        cls = iz.champion_class(alias, role)
        owned = [int(i) for i in s.get("items") or [] if int(i) in items]
        build = [items[i].name for i in owned if items[i].kind in ("legendary", "boots")]
        out: list[dict[str, Any]] = []
        seen: set[int] = set()

        def add(iid: int, why: str) -> None:
            it = items.get(int(iid))
            if it is not None and it.rift and iid not in seen and it.kind in ("legendary", "boots"):
                seen.add(iid)
                out.append({"n": it.name, "po": it.gold, "pourquoi": why[:90]})

        for iid in owned:
            add(iid, "dans ton build final")
        enemies = [SimpleNamespace(champion_alias=e.get("alias"), champion_name=e.get("name"), kills=0, deaths=0,
                                   level=11, items=[])
                   for e in s.get("enemies") or [] if isinstance(e, dict) and e.get("alias")]
        prof = iz.enemy_profile(enemies, items)
        for need, sev in sorted(prof.needs.items(), key=lambda kv: -kv[1]):
            if sev < iz.NEED_MIN:
                continue
            why = iz.REASONS[need].format(names=iz._join(prof.names.get(need) or []) or "Les ennemis")
            for iid in iz.NEED_ITEMS.get(need, {}).get(cls, ())[:2]:
                add(iid, why)
        for iid in iz.CORE.get(cls, ()):
            add(iid, "objet de base de ta classe")
        boots = iz.BOOTS_CLASS.get(cls)
        if boots:
            add(boots, "bottes de ta classe")
        for b in set(iz.BOOTS_VS.values()):
            add(b, "bottes défensives")
        return out[:limit], build
    except Exception:
        log.debug("review item candidates unavailable", exc_info=True)
        return [], []


def _allowed_ids(names: Iterable[str]) -> set[int]:
    """Ids of the allowed items and of their components (the path to an allowed item is fine)."""
    try:
        from treeaicoach import itemization as iz

        items = iz.load_items()
        want = {_fold(n) for n in names}
        ids = {iid for iid, it in items.items() if _fold(it.name) in want}
        stack = list(ids)
        while stack:
            it = items.get(stack.pop())
            for p in it.parts if it is not None else ():
                if p not in ids:
                    ids.add(p)
                    stack.append(p)
        return ids
    except Exception:
        return set()


def ground_review(text: str, allowed: Iterable[dict[str, Any]]) -> tuple[str, int]:
    """``(review, number of sentences removed)``: every sentence naming an item outside the allowed
    list (or its components) is dropped; English names of allowed items are replaced by the French
    ones. Never raises (the text unchanged on error)."""
    try:
        cands = [c for c in allowed or () if c.get("n")]
        ok = _allowed_ids(c["n"] for c in cands)
        if not ok:
            return text, 0
        from treeaicoach import itemization as iz

        items = iz.load_items()
        removed = 0
        paras: list[str] = []
        for para in str(text).split("\n"):
            keep: list[str] = []
            for sent in re.findall(r"[^.!?]+[.!?]*", para):
                ids = items_mentioned(sent)
                bad = [i for i in ids if (i >= 0 and i not in ok) or (i < 0 and not (_AMBIG.get(i, frozenset()) & ok))]
                if bad:
                    removed += 1
                    log.info("AI review: sentence with an item outside the list dropped: %r", sent.strip()[:120])
                    continue
                keep.append(_french_names(sent, ids, items))
            joined = " ".join(x.strip() for x in keep if x.strip())
            if joined:
                paras.append(joined)
        return "\n".join(paras), removed
    except Exception:
        log.debug("ground_review failed", exc_info=True)
        return text, 0


def _french_names(sentence: str, ids: set[int], items: dict) -> str:
    """Replace the English name of each allowed item by its French name ("Sterak Gage" ->
    "Gage de Sterak")."""
    for iid in ids:
        en, fr = _english_name(iid), getattr(items.get(iid), "name", "") if iid >= 0 else ""
        if not en or not fr or _fold(en) == _fold(fr):
            continue
        toks = re.findall(r"[A-Za-z0-9]+", re.sub(r"['’]s\b", "", en))
        if not toks:
            continue
        pat = r"\b" + r"(?:['’]?s)?[\s\-]+".join(re.escape(x) for x in toks) + r"(?:['’]?s)?\b"
        sentence = re.sub(pat, fr, sentence, flags=re.I)
    return sentence


_EN_NAMES: dict[int, str] | None = None


def _english_name(iid: int) -> str:
    global _EN_NAMES
    if _EN_NAMES is None:
        try:
            from treeaicoach.paths import asset_path

            data = json.loads(asset_path("items.json").read_text(encoding="utf-8")).get("items") or {}
            _EN_NAMES = {int(k): str(v.get("en") or "") for k, v in data.items() if str(k).isdigit()}
        except Exception:
            _EN_NAMES = {}
    return _EN_NAMES.get(int(iid), "")


def _review_numbers(analysis: Any) -> str:
    """The key numbers the review must cite (plain French)."""
    a = analysis if isinstance(analysis, dict) else {}
    s = a.get("summary") if isinstance(a.get("summary"), dict) else {}
    parts = [f"{s.get('kills', 0)}/{s.get('deaths', 0)}/{s.get('assists', 0)}"]
    if s.get("cs_per_min") is not None:
        parts.append(f"{s.get('cs_per_min')} CS/min")
    v = a.get("death_verdicts") if isinstance(a.get("death_verdicts"), dict) else {}
    if v:
        parts.append("morts : " + ", ".join(f"{n} {k}" for k, n in v.items() if n))
    lead = a.get("alert_lead") if isinstance(a.get("alert_lead"), dict) else {}
    if lead.get("n"):
        parts.append(f"alertes {lead.get('mean')} s avant les morts en moyenne")
    return " ; ".join(parts)


def _looks_english(text: str) -> bool:
    return len(_EN_WORDS.findall(text or "")) > REVIEW_MAX_EN_WORDS


def postgame_review(cfg: Any, analysis: Any, *, caller: Callable[..., Any] = call_llm,
                    url: str | None = None) -> str | None:
    """Blocking AI review of a finished game (None if no provider / on error). ONE request.

    Grounded like the live advisor: the prompt carries my final build and the list of allowed
    items (French names, itemization.py); the answer is validated afterwards: sentences naming
    another item are dropped (an off-meta / impossible / English item never reaches the report),
    English names of allowed items become French, an English answer is rejected. Never raises."""
    prov = str(getattr(cfg, "ai_provider", "off") or "off").lower()
    if provider_spec(prov) is None:
        return None
    try:
        cands, build = review_item_candidates(analysis)
        data = json.dumps(compact_analysis(analysis), ensure_ascii=False, separators=(",", ":"))
        names = ", ".join(c["n"] for c in cands)
        prompt = (f"Analyse de la partie (JSON) : {data}\n"
                  f"Build final du joueur : {', '.join(build) or 'inconnu'}.\n"
                  f"Objets autorisés (noms exacts) : {names or 'aucun : ne conseille aucun objet'}.\n"
                  f"Chiffres à citer : {_review_numbers(analysis)}.")
        text = caller(prov, str(getattr(cfg, "ai_api_key", "") or ""), str(getattr(cfg, "ai_model", "") or ""),
                      REVIEW_PROMPT, prompt, timeout=REVIEW_TIMEOUT_S,
                      url=url, max_tokens=REVIEW_MAX_TOKENS, long=True) or None
        if not text:
            return None
        if _looks_english(text):
            log.info("AI post-game review rejected: not in French")
            return None
        if cands:
            text, removed = ground_review(text, cands)
            if removed and len(cands) >= 2:
                c1, c2 = cands[0], cands[1]
                text = (text + "\n" if text else "") + (
                    f"Objets : garde {c1['n']} ({c1['pourquoi']}) et vise {c2['n']} ({c2['pourquoi']}).")
        return text or None
    except AIError as exc:
        log.info("AI post-game review unavailable (%s)", exc.code)
        return None
    except Exception:
        log.exception("AI post-game review failed")
        return None


def append_review_html(html_path: Any, review: str, provider: str = "") -> bool:
    """Insert a "Revue de l'IA" section in a report (before ``</body>``). Never raises."""
    try:
        import html as _html
        from pathlib import Path

        p = Path(html_path)
        doc = p.read_text(encoding="utf-8")
        label = PROVIDERS[provider].label if provider in PROVIDERS else "IA"
        paras = "".join(f"<p>{_html.escape(x)}</p>" for x in str(review).splitlines() if x.strip())
        block = (f'\n<section class="card ai-review" id="ai-review" style="margin:24px auto;max-width:960px;'
                 f'padding:16px 20px;border:1px solid #785A28;border-radius:12px">'
                 f"<h2>Revue de l'IA</h2>{paras}<p style=\"opacity:.7;font-size:.85em\">Générée par "
                 f"{_html.escape(label)} à partir de l'analyse ci-dessus ; à prendre comme un avis.</p></section>\n")
        low = doc.lower()
        i = low.rfind("</body>")
        doc = doc[:i] + block + doc[i:] if i >= 0 else doc + block
        p.write_text(doc, encoding="utf-8")
        return True
    except Exception:
        log.exception("Cannot add the AI review to the report")
        return False


def check_connection(cfg: Any, *, caller: Callable[..., str] = call_llm,
                     url: str | None = None) -> tuple[bool, str]:
    """Blocking "Tester" button helper: ``(ok, French message)``. Never raises."""
    prov = str(getattr(cfg, "ai_provider", "off") or "off").lower()
    spec = provider_spec(prov)
    if spec is None:
        return False, "Choisis d'abord un fournisseur d'IA."
    prompt = ("Moment : test de connexion. Donne un conseil générique d'une phrase pour un joueur de "
              "League of Legends qui revient en base avec 1300 pièces d'or.")
    try:
        text = caller(prov, str(getattr(cfg, "ai_api_key", "") or ""), str(getattr(cfg, "ai_model", "") or ""),
                      system_prompt(), prompt, timeout=TIMEOUT_S, url=url)
        return True, f"Connexion OK ({spec.label}) : {text}"
    except AIError as exc:
        return False, error_text(exc.code, prov)
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("AI test failed")
        return False, f"Conseil IA : erreur inattendue ({type(exc).__name__})."
