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
REVIEW_RATE_WAIT_S = 30.0       # post-game review: a per-minute 429 asking <= this is waited out once
MAX_ANALYSIS_BYTES = 12000
MANUAL_MIN_INTERVAL_S = 20.0
PLAN_MAX_AGE_S = 25.0           # an automatic plan not shown within this (fight, card) is dropped
MANUAL_MAX_AGE_S = 60.0         # the answer to F8 waits longer (the player asked for it)
DEFER_S = 30.0                  # a key moment during a fight / gank is retried this long after it
TREND_WINDOW_S = 120.0          # team gold trend measured over this (game time)
TIMELINE_EVERY_S = 120.0        # one timeline line every 2 min of game time (post-game review)
GROQ_MIN_TOKENS = 500
GEMINI_MIN_TOKENS = 1024        # Gemini 2.5 / 3 count their (reduced) thinking in maxOutputTokens
#: auto-pick order when the configured / default model does not exist for the key
#: (Groq, 2026-10: llama-3.3-70b and llama-3.1-8b are gone; the live list is gpt-oss-120b / 20b, qwen3.8)
MODEL_PREFERENCE: dict[str, tuple[str, ...]] = {
    "groq": ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b", "llama-3.3-70b-versatile",
             "qwen/qwen3-32b", "meta-llama/llama-4-maverick-17b-128e-instruct", "llama-3.1-8b-instant"),
    "openrouter": ("openai/gpt-oss-120b:free", "meta-llama/llama-3.3-70b-instruct:free",
                   "deepseek/deepseek-chat-v3-0324:free", "google/gemini-2.0-flash-exp:free"),
    "gemini": ("gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.1-flash-lite",
               "gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash"),
}
#: never auto-picked: audio / speech / safety classifiers / embeddings (not chat models)
_NOT_CHAT = ("whisper", "guard", "safeguard", "tts", "orpheus", "playai", "distil", "embed", "moderation",
             "allam", "compound", "image", "vision-only", "aqa", "imagen", "veo", "live", "audio")
#: substitutions learnt when a model does not exist for the key: (provider, requested model) -> model used
_auto_model: dict[tuple[str, str], str] = {}
RATE_RETRY_MAX_S = 3.0          # a per-minute limit (429) asking to wait <= this: wait and retry ONCE
RATE_BACKOFF_MIN_S, RATE_BACKOFF_MAX_S = 15.0, 120.0
BASE_GOLD_MIN = 800             # "base visit with gold"
FED_LEVELS = (6, 11, 16)
OBJECTIVE_LEAD_S = 80.0
OBJECTIVE_WINDOW_S = 20.0       # announced once the remaining time is within 80 s +/- this (60..100 s)
OBJECTIVE_KEYS = ("dragon", "baron", "elder")
MAJOR_DRAGON_GT = 14 * 60.0     # a dragon is a "major" objective (budget slot) from 14:00
#: back-off after an error (seconds before the next automatic request)
#: "rate" = a per-minute limit (429 + a short "try again in 7 s"): transient, the real wait is used;
#: "quota" = the daily / account quota (429 per day, insufficient_quota): long back-off.
BACKOFF_S: dict[str, float] = {"key": math.inf, "nokey": math.inf, "quota": 600.0, "offline": 300.0,
                               "model": math.inf, "server": 180.0, "empty": 90.0, "bad": 180.0, "rate": 30.0}
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
    # gemini-2.0 / 2.5 are restricted to accounts that already used them (2026): new keys get a 404
    "gemini": Provider("gemini", "Google Gemini (gratuit)",
                       "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                       "gemini-3.5-flash-lite", True, "https://aistudio.google.com/apikey"),
    "groq": Provider("groq", "Groq (gratuit)", "https://api.groq.com/openai/v1/chat/completions",
                     "openai/gpt-oss-120b", True, "https://console.groq.com/keys"),
    "openrouter": Provider("openrouter", "OpenRouter (modèles :free)",
                           "https://openrouter.ai/api/v1/chat/completions",
                           "meta-llama/llama-3.3-70b-instruct:free", True, "https://openrouter.ai/keys"),
    "ollama": Provider("ollama", "Ollama (local, sans clé)", "http://127.0.0.1:11434/api/chat",
                       "llama3.1", False, "https://ollama.com"),
    "anthropic": Provider("anthropic", "Anthropic Claude", "https://api.anthropic.com/v1/messages",
                          "claude-haiku-4-5", True, "https://console.anthropic.com/settings/keys"),
}
PROVIDER_CHOICES: tuple[tuple[str, str], ...] = (("off", "Désactivé"),) + tuple(
    (p.key, p.label) for p in PROVIDERS.values())

ERROR_FR: dict[str, str] = {
    "key": "Conseil IA : clé API invalide ou refusée.",
    "nokey": "Conseil IA : aucune clé API saisie.",
    "quota": "Conseil IA : quota gratuit atteint, nouvel essai dans 10 minutes.",
    "rate": "Conseil IA : trop de demandes en une minute, nouvel essai dans quelques secondes.",
    "offline": "Conseil IA : service injoignable (hors ligne ?).",
    "model": "Conseil IA : modèle introuvable, vérifie son nom.",
    "server": "Conseil IA : le service a renvoyé une erreur.",
    "empty": "Conseil IA : réponse vide.",
    "bad": "Conseil IA : réponse illisible.",
}
OLLAMA_OFFLINE = "Conseil IA : Ollama ne répond pas (lance « ollama serve »)."

SYSTEM_PROMPT = (
    "Tu es le coach d'un joueur de League of Legends pendant sa partie. Tu reçois l'état complet de la "
    "partie en JSON (API officielle du jeu + analyse de TreeAI). Règles : "
    "1) Français uniquement, tutoiement, aucun mot anglais (balise, vague, sbires, objet, jungler). "
    "2) Chaque phrase commence par un verbe à l'impératif (Rentre, Pousse, Pose, Prends, Recule, Achète...). "
    "3) Un plan pour les 30 à 90 prochaines secondes, concret : quel lieu, quel temps (obj), quel objet exact. "
    "4) Seulement les faits du JSON : ne cite jamais les clés du JSON, n'invente aucun chiffre, aucune "
    "spéculation sur les temps de recharge, sorts d'invocateur ou ultimes ennemis. "
    "5) carte = l'appel actif affiché par le coach : ton plan le suit et le prolonge ; si le JSON montre "
    "clairement mieux, carte=differe et pourquoi = 1 ligne courte. "
    "6) Objets : seulement ceux de objets_possibles ou de achat, noms exacts, jamais un objet déjà dans me.it ; "
    "tiens compte de effets (anti-soin, stase, armure ennemie), de compo, du jungler (jgl) et de mes morts. "
    "7) Réponds uniquement avec l'objet JSON demandé, sans markdown ni introduction."
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

    def __init__(self, code: str, detail: str = "", retry_after: float | None = None) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.retry_after = retry_after      # seconds the provider asked to wait ("rate"), when known


def provider_spec(name: Any) -> Provider | None:
    return PROVIDERS.get(str(name or "").strip().lower())


def error_text(code: str, provider: str | None = None) -> str:
    if code == "offline" and provider == "ollama":
        return OLLAMA_OFFLINE
    return ERROR_FR.get(code, ERROR_FR["server"])


# ======================================================================================
# HTTP: request building / response parsing (pure) + the urllib call
# ======================================================================================
def _gemini_thinking(model: str) -> dict[str, Any] | None:
    """Gemini thinking control: 2.5 Flash / Flash-Lite can switch it off (budget 0), 2.5 Pro needs
    at least 128, Gemini 3 takes a level. Without it a 2.5 / 3 model spends the whole output budget
    thinking and answers nothing (``finishReason`` MAX_TOKENS, no text)."""
    m = model.lower().split("/")[-1]
    if m.startswith("gemini-2.5"):
        return {"thinkingBudget": 128} if "pro" in m else {"thinkingBudget": 0}
    if m.startswith("gemini-3"):
        return {"thinkingLevel": "low"}
    return None


#: Ollama models that think: ``think`` must be sent (False, or a level for gpt-oss); sending it to
#: a model that does not think is a 400 ("does not support thinking"), so only for these names.
_OLLAMA_THINKERS = ("qwen3", "deepseek-r1", "magistral", "gpt-oss", "phi4-reasoning", "cogito")


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
        gen: dict[str, Any] = {"maxOutputTokens": max_tokens, "temperature": 0.4}
        thinking = _gemini_thinking(model)
        if thinking is not None:
            gen["thinkingConfig"] = thinking
            gen["maxOutputTokens"] = max(max_tokens, GEMINI_MIN_TOKENS)
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": gen,
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
        elif "gpt-oss" in model or "deepseek-r1" in model or "qwen3" in model:
            # OpenRouter: unified reasoning switch; low effort and never returned in the content
            body["reasoning"] = {"effort": "low", "exclude": True}
            body["max_tokens"] = max(max_tokens, GROQ_MIN_TOKENS)
        if json_mode:
            body["response_format"] = {"type": "json_object"}
    elif provider == "ollama":
        target = url or spec.url
        body = {"model": model, "stream": False, "options": {"num_predict": max_tokens, "temperature": 0.4},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        low = model.lower()
        if any(w in low for w in _OLLAMA_THINKERS):
            body["think"] = "low" if "gpt-oss" in low else False
            body["options"]["num_predict"] = max(max_tokens, GROQ_MIN_TOKENS)
        if json_mode:
            body["format"] = "json"
    else:  # anthropic
        target = url or spec.url
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
        body = {"model": model, "max_tokens": max_tokens, "system": system,
                "messages": [{"role": "user", "content": prompt}]}
    return target, headers, json.dumps(body, ensure_ascii=False).encode("utf-8")


def _body_error(provider: str, data: dict[str, Any]) -> AIError | None:
    """An error carried in a 200 answer (OpenRouter forwards the upstream provider's 429 / 5xx
    as ``{"error": {"code": 429, ...}}`` with HTTP 200; Gemini blocks a prompt with
    ``promptFeedback.blockReason``; Anthropic declines with ``stop_reason: "refusal"``)."""
    err = data.get("error")
    if isinstance(err, dict) or isinstance(err, str):
        e = err if isinstance(err, dict) else {"message": err}
        try:
            code = int(e.get("code") or 0)
        except (TypeError, ValueError):
            code = 0
        body = json.dumps(e, ensure_ascii=False)[:2000]
        kind, wait = _classify_http(code or 500, body, None)
        return AIError(kind, f"in-body error {code}", retry_after=wait)
    if provider == "gemini":
        fb = data.get("promptFeedback") or {}
        if isinstance(fb, dict) and fb.get("blockReason") and not data.get("candidates"):
            return AIError("empty", f"blocked: {fb.get('blockReason')}")
    if provider == "anthropic" and data.get("stop_reason") == "refusal":
        return AIError("empty", "refusal")
    return None


def parse_response(provider: str, data: Any, long: bool = False, raw: bool = False) -> str:
    """Text of a provider's JSON answer (``raw``: only the reasoning removed, for a JSON plan).
    Raises :class:`AIError` ("empty" / "bad", or the error carried in the body)."""
    try:
        err = _body_error(provider, data) if isinstance(data, dict) else None
        if err is not None:
            raise err
        if provider == "gemini":
            cands = data.get("candidates") or []
            parts = ((cands[0] or {}).get("content") or {}).get("parts") or [] if cands else []
            text = "".join(str(p.get("text") or "") for p in parts
                           if isinstance(p, dict) and not p.get("thought"))      # thought summaries: never shown
        elif provider in ("groq", "openrouter"):
            choices = data.get("choices") or []
            text = str(((choices[0] or {}).get("message") or {}).get("content") or "") if choices else ""
        elif provider == "ollama":
            text = str((data.get("message") or {}).get("content") or "")         # message.thinking ignored
        else:
            text = "".join(str(b.get("text") or "") for b in data.get("content") or []
                           if isinstance(b, dict) and b.get("type", "text") == "text")
    except (AttributeError, TypeError, IndexError, KeyError) as exc:
        raise AIError("bad", str(exc)) from exc
    if raw:
        text = re.sub(r"<think>.*?</think>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<think>.*$", " ", text, flags=re.S | re.I).strip()[:4000]    # unclosed (truncated)
    else:
        text = clean_review(text) if long else clean_advice(text)
    if not text:
        raise AIError("empty")
    return text


def clean_advice(text: Any) -> str:
    """Plain text, no markdown, at most 2 sentences / :data:`MAX_ADVICE_CHARS` characters."""
    s = str(text or "")
    s = re.sub(r"<think>.*?</think>", " ", s, flags=re.S | re.I)    # reasoning models
    s = re.sub(r"<think>.*$", " ", s, flags=re.S | re.I)            # ... cut before closing it
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


_WAIT_RE = re.compile(r"(?:try again in|retry in|retrydelay\W*)\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?!s))?\s*"
                      r"(?:(\d+(?:\.\d+)?)\s*s)?\s*(?:(\d+(?:\.\d+)?)\s*ms)?", re.I)
#: daily / account quota markers. NOT "billing": Groq's per-minute 429 ends with "Upgrade to Dev Tier
#: ... /settings/billing" (live, 2026-10) and was taken for the daily quota (10 min back-off)
_DAILY = ("per day", "(rpd)", "(tpd)", "perday", "daily", "insufficient_quota", "requests_per_day",
          "free_tier_requests_per_day", "credit balance")


def _retry_after(body: str, headers: Any = None) -> float | None:
    """Seconds the provider asks to wait: ``Retry-After`` header, else "try again in 1m2.5s" /
    Gemini ``"retryDelay": "34s"`` in the body. None when not given."""
    try:
        v = headers.get("Retry-After") if headers is not None else None
        if v not in (None, ""):
            return max(0.0, float(v))
    except (TypeError, ValueError, AttributeError):
        pass
    for m in _WAIT_RE.finditer(body or ""):
        h, mi, sec, ms = m.groups()
        if not any((h, mi, sec, ms)):
            continue
        return (float(h or 0) * 3600 + float(mi or 0) * 60 + float(sec or 0) + float(ms or 0) / 1000.0)
    return None


def _classify_http(code: int, body: str, headers: Any = None) -> tuple[str, float | None]:
    """``(error code, seconds to wait or None)`` of an HTTP error.

    A 429 is split: a per-minute limit ("rate": Groq TPM / RPM, Gemini per-minute, OpenRouter
    upstream) waits the time the provider gives; the daily / account quota ("quota") backs off
    long. 529 / 503 "overloaded" are transient service errors."""
    low = (body or "").lower()
    if code in (401, 403) and "no longer available" not in low and "not found" not in low:
        return "key", None
    if code == 400 and ("api key" in low or "api_key" in low or "apikey" in low) and "model" not in low:
        return "key", None
    if ("model_not_found" in low or "does not exist" in low or "decommissioned" in low
            or "no longer available" in low or "is not found for api version" in low
            or "not supported for generatecontent" in low or ("model" in low and "not found" in low)):
        return "model", None
    if code == 429 or "rate limit" in low or "rate_limit" in low or "resource_exhausted" in low or "quota" in low:
        wait = _retry_after(body, headers)
        daily = any(w in low for w in _DAILY) or (wait is not None and wait > 600.0)
        return ("quota" if daily else "rate"), wait
    if code == 404:
        return "model", None
    return "server", None


def _opener_for(url: str) -> urllib.request.OpenerDirector:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in LOOPBACK_HOSTS:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never proxy localhost
    return urllib.request.build_opener()


def list_models(provider: str, api_key: str, url: str | None = None, timeout: float = TIMEOUT_S) -> list[str]:
    """Chat model ids available for this key (GET .../models; Gemini: the models supporting
    ``generateContent``). [] on any error."""
    spec = provider_spec(provider)
    if spec is None or provider not in ("groq", "openrouter", "gemini"):
        return []
    headers = {"Accept": "application/json", "User-Agent": "TreeAICoach"}
    if provider == "gemini":
        target = (url or spec.url).split("/models/", 1)[0] + "/models?pageSize=200"
        headers["x-goog-api-key"] = api_key.strip()
    else:
        target = (url or spec.url).rsplit("/chat/completions", 1)[0] + "/models"
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    req = urllib.request.Request(target, headers=headers)
    try:
        with _opener_for(target).open(req, timeout=timeout) as resp:
            data = json.loads(resp.read(MAX_RESPONSE_BYTES * 4).decode("utf-8", "replace"))
        if provider == "gemini":
            return [str(m.get("name", "")).split("/", 1)[-1] for m in data.get("models") or []
                    if isinstance(m, dict) and "generateContent" in (m.get("supportedGenerationMethods") or [])]
        return [str(m.get("id")) for m in data.get("data") or [] if isinstance(m, dict) and m.get("id")]
    except Exception:
        log.debug("Model list unavailable", exc_info=True)
        return []


def pick_model(provider: str, ids: Iterable[str]) -> str | None:
    """Best chat model among ``ids`` (preference list, then any non-audio / non-guard model)."""
    ids = sorted(i for i in ids if not any(w in i.lower() for w in _NOT_CHAT))
    for pref in MODEL_PREFERENCE.get(provider, ()):
        if pref in ids:
            return pref
    if provider == "openrouter":
        ids = [i for i in ids if i.endswith(":free")]
    if provider == "gemini":
        ids = [i for i in ids if "flash" in i] or ids
    return ids[0] if ids else None


def call_llm(provider: str, api_key: str, model: str, system: str, prompt: str,
             timeout: float = TIMEOUT_S, url: str | None = None, max_tokens: int = MAX_OUTPUT_TOKENS,
             long: bool = False, json_mode: bool = False) -> str:
    """One blocking request; returns the cleaned advice. Raises :class:`AIError` only.

    * a model that does not exist for this key (decommissioned / restricted): the available
      models are listed once, the best chat model is picked and the substitution remembered
      (later calls go straight to it, no failed request first);
    * a per-minute rate limit asking to wait <= :data:`RATE_RETRY_MAX_S`: wait, retry once."""
    want = (model or "").strip()
    model = _auto_model.get((provider, want), want)
    try:
        return _call_rate_retry(provider, api_key, model, system, prompt, timeout, url, max_tokens, long, json_mode)
    except AIError as exc:
        if exc.code != "model" or provider not in ("groq", "openrouter", "gemini"):
            raise
        best = pick_model(provider, list_models(provider, api_key, url, timeout))
        if not best or best == (model or (provider_spec(provider).default_model if provider_spec(provider) else "")):
            raise
        log.info("AI model %r unavailable, using %r", model or "default", best)
        _auto_model[(provider, want)] = best
        return _call_rate_retry(provider, api_key, best, system, prompt, timeout, url, max_tokens, long, json_mode)


def _call_rate_retry(provider: str, api_key: str, model: str, system: str, prompt: str, timeout: float,
                     url: str | None, max_tokens: int, long: bool, json_mode: bool) -> str:
    try:
        return _call_once(provider, api_key, model, system, prompt, timeout, url, max_tokens, long, json_mode)
    except AIError as exc:
        wait = exc.retry_after
        if exc.code != "rate" or wait is None or wait > RATE_RETRY_MAX_S:
            raise
        log.info("AI rate limit (%s): retry in %.1f s", provider, wait)
        time.sleep(max(0.2, wait + 0.2))
        return _call_once(provider, api_key, model, system, prompt, timeout, url, max_tokens, long, json_mode)


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
        kind, wait = _classify_http(int(exc.code), detail, getattr(exc, "headers", None))
        raise AIError(kind, f"HTTP {exc.code}", retry_after=wait) from None
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
CARD_STANCES = ("suit", "differe", "aucune")      # the plan vs the live game-changer card ("carte")
MAX_WHY_CHARS = 90
PLAN_SCHEMA_FR = ('{"plan": "1 phrase courte (20 mots max) qui commence par un verbe à l\'impératif", '
                  '"etapes": ["un détail NOUVEAU (lieu, temps, objet), verbe à l\'impératif, 15 mots max", '
                  '"... (0 à 3)"], '
                  '"objectif": "' + "|".join(PLAN_GOALS) + '", "urgence": "haute|moyenne|basse", '
                  '"carte": "suit|differe|aucune", "pourquoi": "si differe : 1 ligne, sinon vide"}')


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


_STR_FIELD = r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"'


def _salvage_plan(text: str) -> dict[str, Any] | None:
    """A plan from a TRUNCATED JSON answer (``finish_reason: length`` / ``max_tokens``): the
    complete ``"plan"`` string and the complete steps written before the cut. None otherwise."""
    s = re.sub(r"<think>.*?</think>", " ", str(text or ""), flags=re.S | re.I)
    i = s.find("{")
    if i < 0:
        return None
    s = s[i:]
    m = re.search(_STR_FIELD % "plan", s)
    if m is None:
        return None
    try:
        plan = json.loads(f'"{m.group(1)}"')
    except ValueError:
        return None
    steps: list[str] = []
    em = re.search(r'"etapes"\s*:\s*\[(.*)', s, flags=re.S)
    if em:
        for sm in re.finditer(r'"((?:[^"\\]|\\.)*)"\s*(?=[,\]])', em.group(1)):
            try:
                steps.append(json.loads(f'"{sm.group(1)}"'))
            except ValueError:
                break
    data: dict[str, Any] = {"plan": plan, "etapes": steps}
    for k in ("objectif", "urgence", "carte", "pourquoi"):
        fm = re.search(_STR_FIELD % k, s)
        if fm:
            data[k] = fm.group(1)
    return data


#: English League words the models slip into French plans (live Groq, 2026-10: "farm", "lane",
#: "last-hit", "ward") -> the French words the app uses everywhere
_EN_TERMS: tuple[tuple[str, str], ...] = (
    (r"\blast[\s\-\u2011]?hit(?:te[rz]?|s)?(?: (?:les|des|tes) sbires)?\b", "achève les sbires"), (r"\bfarm(?:e[rz]?|s)?\b", "farme"),
    (r"\b(?:en|ta|la|sa|ma) lane\b", "dans ta voie"), (r"\blanes?\b", "voie"), (r"\bwards?\b", "balise"),
    (r"\bcontrol ward\b", "balise de contrôle"), (r"\bpush(?:e[rz]?|s)?\b", "pousse"),
    (r"\bback(?:e[rz]?)?\b", "rentre"), (r"\btrade(?:s|r)?\b", "échange"), (r"\bfreeze\b", "gèle la vague"),
    (r"\bteamfights?\b", "combat d'équipe"), (r"\bsplit[\s\-]?push\b", "pousse seul"),
    (r"\bpiton\b", "pit"))


def frenchify(text: Any) -> str:
    """Replace the English League jargon of a French line by the app's French words."""
    s = str(text or "").replace("\u2011", "-")
    for pat, fr in _EN_TERMS:
        s = re.sub(pat, fr, s, flags=re.I)
    return s


def _redundant(step: str, plan: str) -> bool:
    """A step that only repeats the plan (>= 70 % of its words already in it)."""
    w = [x for x in re.findall(r"\w{3,}", step.lower())]
    pw = set(re.findall(r"\w{3,}", plan.lower()))
    return bool(w) and sum(1 for x in w if x in pw) / len(w) >= 0.7


def parse_plan(text: Any) -> dict[str, Any] | None:
    """Strict parse of the model's JSON plan: ``{"plan", "etapes", "objectif", "urgence", "carte",
    "pourquoi"}`` or None.

    ``plan`` must be a non-empty string; ``etapes`` a list of strings (at most :data:`MAX_STEPS`
    kept); unknown ``objectif`` / ``urgence`` / ``carte`` values are dropped; extra keys ignored.
    A truncated answer keeps its complete ``plan`` and steps (:func:`_salvage_plan`). Never raises."""
    try:
        block = _json_block(text)
        data: Any = None
        if block is not None:
            try:
                data = json.loads(block)
            except ValueError:
                data = None
        if data is None:
            s = str(text or "")
            if "{" not in s or (block is not None and s.rstrip().endswith("}")):
                return None                       # complete but broken JSON: rejected
            data = _salvage_plan(s)               # cut before its end: keep what is complete
            if data is None:
                return None
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
        plan = frenchify(plan)
        steps = [_short(frenchify(x), MAX_STEP_CHARS) for x in steps_raw if x.strip()]
        steps = [x for x in steps if not _redundant(x, plan)][:MAX_STEPS]
        goal = str(data.get("objectif") or "").strip().lower()
        goal = unicodedata.normalize("NFKD", goal).encode("ascii", "ignore").decode()
        urg = str(data.get("urgence") or "").strip().lower()
        card = unicodedata.normalize("NFKD", str(data.get("carte") or "").strip().lower()).encode(
            "ascii", "ignore").decode()
        why = _short(data.get("pourquoi"), MAX_WHY_CHARS) if isinstance(data.get("pourquoi"), str) else ""
        return {"plan": _short(plan, MAX_PLAN_CHARS), "etapes": steps,
                "objectif": goal if goal in PLAN_GOALS else None,
                "urgence": urg if urg in PLAN_URGENCY else None,
                "carte": card if card in CARD_STANCES else None,
                "pourquoi": why if len(why) >= 4 else None}
    except (ValueError, TypeError):
        return None


def plan_text(plan: dict[str, Any]) -> str:
    """One display line: the plan, the reason it departs from the live card (if it does), then
    the steps (bounded like an advice)."""
    why = str(plan.get("pourquoi") or "").strip() if plan.get("carte") == "differe" else ""
    parts = [str(plan.get("plan") or "").strip()] + ([f"Pourquoi pas la carte : {why}"] if why else []) + [
        str(x).strip() for x in plan.get("etapes") or []]
    parts = [p if p.endswith((".", "!", "?", "…")) else p + "." for p in parts if p]
    s = " ".join(parts)
    if len(s) > MAX_ADVICE_CHARS:
        cut = s[:MAX_ADVICE_CHARS]
        s = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(",;: ") + "…"
    return s


# ---------------------------------------------------------------- consistency with the live card
_RETREAT_RE = re.compile(
    r"\b(recule[rz]?|replie[rz]?|repli|d[ée]fends?|reste sous|sous ta tour|ne force pas|ne te bats pas|"
    r"n'engage pas|[ée]vite|joue (?:la )?s[ée]curit[ée]|joue safe|ne trade pas|farme sous|rentre te soigner|"
    r"ne va pas|ne te montre pas|pas de buisson)\b", re.I)
_GO_RE = re.compile(
    r"\b(prends|frappe|force|engage|avance|attaque|plaque|tue|punis|investis|joue agressif|"
    r"va (?:au|à|a|sur|mid|top|bot|prendre)|all-?in|trade)\b", re.I)
_OBJ_WORDS = {"baron": "baron", "nashor": "baron", "dragon": "dragon", "ancestral": "dragon", "héraut": "heraut",
              "heraut": "heraut", "larves": "larves"}


def stance_of(text: Any) -> str | None:
    """"retreat" / "go" from the first imperative of a French line (the main clause decides:
    "Recule, puis prends le dragon" is a retreat now), None when neutral."""
    s = str(text or "")
    head = re.split(r"[.:;!?]| puis | ensuite ", s, maxsplit=1)[0]
    for part in (head, s):
        r, g = _RETREAT_RE.search(part), _GO_RE.search(part)
        if r or g:
            if r and g:
                return "retreat" if r.start() < g.start() else "go"
            return "retreat" if r else "go"
    return None


def _objective_of(text: Any) -> str | None:
    low = str(text or "").lower()
    hits = [(low.find(w), g) for w, g in _OBJ_WORDS.items() if w in low]
    return min(hits)[1] if hits else None


def card_check(advice_text: Any, card: Any, *, stance: str | None = None, why: str | None = None,
               goal: str | None = None) -> str:
    """How an AI plan stands against the live game-changer card (``card``: its text, or a
    :class:`treeaicoach.macro.GeniusCall`): ``"ok"`` (same call, or no card), ``"explained"`` (it
    contradicts the card but the model said why in one line: shown once the card is gone),
    ``"conflict"`` (contradicts the card without a reason: never shown). Never raises."""
    try:
        ctext = str(getattr(card, "text", card) or "").strip()
        if not ctext:
            return "ok"
        a_st, c_st = stance_of(advice_text), stance_of(ctext)
        clash = a_st is not None and c_st is not None and a_st != c_st
        c_obj = _objective_of(ctext)
        a_obj = goal if goal in ("dragon", "baron", "heraut", "larves") else _objective_of(
            re.split(r"[.!?]", str(advice_text or ""), maxsplit=1)[0])
        if c_obj and a_obj and c_obj != a_obj and c_st == "go" and a_st == "go":
            clash = True                     # "prends le Baron" vs "prends le dragon": two places at once
        if not clash:
            return "ok"
        return "explained" if (stance == "differe" and why) else "conflict"
    except Exception:
        return "ok"


# ======================================================================================
# Game snapshot + prompt
# ======================================================================================
#: Keys of the snapshot (sent once in the system prompt). One compact "mastermind" state: everything
#: TreeAI knows (Live Client + minimap + its own analysis), deterministic order, < 1.5k tokens.
LEGEND = ("Clés : t=temps de jeu, ph=phase, mo=moment, carte=appel ACTIF de la carte du coach (appel + "
          "pourquoi), me=moi (c champion, r rôle, lv niveau, k K/D/A, cs, g or dispo, pv %, it objets, "
          "ig valeur des objets, rs réapparition s), voie=mon duel (vs adversaire, niv/po/cs écarts + = pour "
          "moi, conseils du matchup), eq=écarts d'équipe (po or, tend évolution sur 2 min, kills, niv), "
          "al/en=alliés/ennemis \"champion rôle niveau K/D/A or-en-objets [objets] [mort Xs]\", compo=profils "
          "des compos (dégâts P/M, styles, courbe de puissance, contrôle) + plan de victoire, effets=objets "
          "ennemis qui changent ton jeu (anti-soin, stase, armure...), jgl=jungler ennemi (vu = où et il y a "
          "combien de s, farm, côté probable), obj=[objectif, s avant apparition, 0=dispo], carto=dragons, "
          "âme, buffs, tours perdues, morts=mes morts récentes avec cause, ev=derniers événements, "
          "coups=mes coups notés + prec précision 0-100, achat=objet conseillé, objets_possibles=objets "
          "autorisés, prio=vagues, balises=balises conseillées, wp=probabilité de victoire %.")
MAX_SNAPSHOT_BYTES = 3900       # ~1.3k tokens (French JSON ~ 2.9 bytes / token, measured on Groq)
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


def _item_list(items: Iterable[Any], big_only: bool = False) -> tuple[list[str], int]:
    """``(French item names, total item gold)`` of an inventory (trinkets left out; ``big_only``:
    finished legendary items and boots only)."""
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
        if info[0] and info[2] not in ("trinket",) and (not big_only or info[2] in ("legendary", "boots")):
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


def _kgold(g: Any) -> str:
    return f"{float(g) / 1000.0:.1f}k"


def _pline(p: Any, side: str, roles: Any) -> str:
    """One player as a short line: "Zed mid 9 3/1/2 6.2k [Couperet noir] mort 23s"."""
    name = str(p.champion_name or p.champion_alias)
    names, gold = _item_list(p.items, big_only=True)
    parts = [name, _role(p, side, roles) or "?", str(int(p.level)), f"{p.kills}/{p.deaths}/{p.assists}",
             _kgold(gold)]
    if names and side == "enemy":
        parts.append("[" + ", ".join(names[:3]) + "]")
    if getattr(p, "is_dead", False):
        parts.append(f"mort {int(max(0.0, float(getattr(p, 'respawn_timer', 0.0) or 0.0)))}s")
    return " ".join(parts)


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
    """Live analysis of the engine for the snapshot: the active game-changer card (``carte``),
    the phase (``ph``), lane / team diffs (``diff``), the matchup tips (``conseils``), both team
    comps (``compo``), enemy item effects (``effets``), the enemy jungler (``jgl``: last seen
    zone + age, Tab intel, probable side), the map state (``carto``: dragons, soul, buffs, towers
    lost), my deaths with their cause (``causes``), rated plays (``coups`` + ``prec``), lane
    priorities (``prio``), ward spots (``balises``), win probability (``wp``) and any analysis
    another module exposes through ``engine.ai_extra_context()`` (``plus``).

    Every piece is optional (guarded): a missing analyser is simply left out. Never raises."""
    ctx: dict[str, Any] = {}
    if engine is None:
        return ctx
    try:
        if now is None:
            clock_fn = getattr(engine, "_clock", None)
            now = float(clock_fn()) if callable(clock_fn) else time.monotonic()
    except Exception:
        now = time.monotonic()
    game = getattr(engine, "_game", None)

    def safe(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception:
            log.debug("AI context section failed", exc_info=True)
            return None

    for key, fn in (("carte", _ctx_card), ("ph", _ctx_phase), ("diff", _ctx_diff), ("conseils", _ctx_lane_tips),
                    ("compo", _ctx_comp), ("effets", _ctx_effects), ("jgl", _ctx_jungle), ("carto", _ctx_map),
                    ("causes", _ctx_death_causes), ("coups", _ctx_plays), ("prio", _ctx_prio),
                    ("balises", _ctx_wards), ("plus", _ctx_extra)):
        val = safe(lambda fn=fn: fn(engine, game, now))
        if val not in (None, "", [], {}):
            ctx[key] = val
    wp = getattr(engine, "win_probability", None)
    p = safe(wp) if callable(wp) else None
    if isinstance(p, (int, float)) and math.isfinite(float(p)):
        ctx["wp"] = int(round(100 * p))
    pl = getattr(engine, "plays_summary", None)
    summ = safe(pl) if callable(pl) else None
    if isinstance(summ, dict) and summ.get("total"):
        ctx["prec"] = summ.get("precision")
    return compact(ctx, max_list=8, max_str=200) or {}


WAVE_FR = {"pushing": "prio (vague chez eux)", "pushed_in": "vague chez nous", "even": "équilibrée"}
PHASE_SHORT = {"laning": "voie", "mid": "milieu", "late": "fin"}
EFFECT_FR = {"antiheal": "anti-soin", "stasis": "stase", "armor": "armure", "mr": "résistance magique",
             "armorpen": "pénétration d'armure", "magicpen": "pénétration magique", "lifesteal": "vol de vie",
             "shield": "boucliers", "tenacity": "ténacité", "cleanse": "purge", "anticrit": "anti-critique",
             "pcthp": "dégâts % PV", "health": "PV"}
#: effects that change MY game (the others are noise for the plan)
EFFECT_KEYS = ("antiheal", "stasis", "armor", "mr", "armorpen", "magicpen", "lifesteal", "shield", "tenacity",
               "cleanse", "anticrit", "pcthp")


def _ctx_card(engine: Any, game: Any, now: float) -> dict[str, Any] | None:
    """The ACTIVE game-changer card (macro.py / game_changers.py, the HUD line + banner):
    ``{"appel", "pourquoi"}``. The AI plan must follow it (or say why not)."""
    tac = getattr(engine, "_tactics", None)
    fn = getattr(tac, "macro_active", None)
    c = fn() if callable(fn) else None
    if c is None:
        return None
    return {"appel": str(c.text)[:110], "pourquoi": str(c.why)[:120]}


def _ctx_phase(engine: Any, game: Any, now: float) -> str | None:
    tac = getattr(engine, "_tactics", None)
    ph = tac.phase() if tac is not None and hasattr(tac, "phase") else None
    if ph is None:
        gt = float(getattr(game, "game_time", 0.0) or 0.0)
        ph = "laning" if gt < 14 * 60 else "mid" if gt < 25 * 60 else "late"
    return PHASE_SHORT.get(str(ph), str(ph))


def _ctx_plays(engine: Any, game: Any, now: float) -> list[str]:
    """My last rated moments (plays.py), most recent last: ``["12:04 blunder : raison"]``."""
    pc = getattr(engine, "_plays", None)
    hist = pc.history() if pc is not None and hasattr(pc, "history") else []
    gt = float(getattr(game, "game_time", 0.0) or 0.0)
    return [f"{_clock(p.gt)} {p.cls} : {str(p.reason)[:70]}" for p in hist if gt - float(p.gt) <= 600.0][-3:]


def _ctx_diff(engine: Any, game: Any, now: float) -> dict[str, Any] | None:
    fn = getattr(engine, "scoreboard_summary", None)
    sb = fn() if callable(fn) else None
    if sb is None or not getattr(sb, "players", None):
        return None
    d: dict[str, Any] = {"eq": int(sb.team_gold_diff)}
    try:
        d["kills"] = f"{int(sb.ally_kills)}-{int(sb.enemy_kills)}"
    except (AttributeError, TypeError, ValueError):
        pass
    m = getattr(sb, "my_matchup", None)
    if m is not None:
        d.update(po=int(m.gold_diff), niv=int(m.level_diff), cs=int(m.cs_diff), vs=str(m.enemy))
        if getattr(m, "enemy_alias", None):
            d["vs_alias"] = str(m.enemy_alias)
    return d


def _ctx_lane_tips(engine: Any, game: Any, now: float) -> list[str] | None:
    """2 matchup lines of ``assets/matchups.json`` (game_plan.lane_lines) for my lane opponent,
    during the laning phase only."""
    from treeaicoach import game_plan

    me = getattr(game, "me", None)
    if me is None or float(getattr(game, "game_time", 0.0) or 0.0) > 16 * 60:
        return None
    fn = getattr(engine, "scoreboard_summary", None)
    sb = fn() if callable(fn) else None
    m = getattr(sb, "my_matchup", None) if sb is not None else None
    if m is None or not getattr(m, "enemy_alias", None):
        return None
    return game_plan.lane_lines(me.champion_alias, m.enemy_alias, str(m.enemy), n=2)


def team_profile(aliases: Iterable[Any]) -> str:
    """A team comp in one line: damage split, play styles, power curve, crowd control
    ("dégâts P3/M2 ; engage 2, poke 1 ; courbe tôt 1/milieu 3/tard 1 ; contrôle 9")."""
    from treeaicoach import meta

    profs = [meta.profile(a) for a in aliases if a]
    if not profs:
        return ""
    dmg = {"P": 0, "M": 0, "X": 0}
    curve = {"early": 0, "mid": 0, "late": 0}
    styles: dict[str, int] = {}
    cc = 0
    for p in profs:
        dmg[p.damage if p.damage in dmg else "X"] += 1
        curve[p.curve if p.curve in curve else "mid"] += 1
        cc += int(p.cc)
        for st in p.style:
            styles[st] = styles.get(st, 0) + 1
    d = f"dégâts P{dmg['P']}/M{dmg['M']}" + (f"/X{dmg['X']}" if dmg["X"] else "")
    st = ", ".join(f"{k} {n}" for k, n in sorted(styles.items(), key=lambda kv: (-kv[1], kv[0]))[:4])
    c = f"courbe tôt {curve['early']}/milieu {curve['mid']}/tard {curve['late']}"
    return " ; ".join(x for x in (d, st, c, f"contrôle {cc}") if x)


def _ctx_comp(engine: Any, game: Any, now: float) -> dict[str, str] | None:
    if game is None or getattr(game, "me", None) is None:
        return None
    fn = getattr(engine, "mastermind_reading", None)            # mastermind.py: comps, windows, win conditions
    r = fn() if callable(fn) else None
    if r is not None and getattr(r, "us", None) is not None:
        out: dict[str, Any] = {"nous": str(r.us.summary())[:150], "eux": str(r.them.summary())[:150]}
        if getattr(r, "team", None) is not None and getattr(r.team, "text", ""):
            out["fenetre"] = str(r.team.text)[:120]
        if getattr(r, "lane", None) is not None and getattr(r.lane, "text", ""):
            out["fenetre_voie"] = str(r.lane.text)[:120]
        if getattr(r, "ours", None):
            out["plan"] = " ; ".join(str(x) for x in list(r.ours)[:2])[:180]
        if getattr(r, "theirs", None):
            out["plan_eux"] = str(list(r.theirs)[0])[:120]
        if getattr(r, "role", ""):
            out["mon_role"] = str(r.role)[:120]
        threats = [t.line() for t in list(getattr(r, "threats", ()) or ())[:2] if hasattr(t, "line")]
        if threats:
            out["menaces"] = [str(x)[:90] for x in threats]
        return out
    ours = [game.me.champion_alias] + [p.champion_alias for p in getattr(game, "allies", []) or []]
    theirs = [p.champion_alias for p in getattr(game, "enemies", []) or []]
    out = {"nous": team_profile(ours), "eux": team_profile(theirs)}
    for name in ("win_condition", "team_comp_summary", "comp_summary"):   # team-comp module (optional)
        fn = getattr(engine, name, None)
        if callable(fn):
            v = fn()
            text = v if isinstance(v, str) else getattr(v, "text", None) or (
                json.dumps(compact(v, max_list=4, max_str=80), ensure_ascii=False) if v else "")
            if text:
                out["plan"] = str(text)[:180]
            break
    return {k: v for k, v in out.items() if v} or None


def _ctx_effects(engine: Any, game: Any, now: float) -> dict[str, list[str]] | None:
    """Enemy item effects that change my game: ``{"anti-soin": ["Darius"], "armure": ["Malphite x2"]}``
    (finished items only; ``x2``: stacked)."""
    from treeaicoach import itemization as iz

    items = iz.load_items()
    out: dict[str, list[str]] = {}
    for p in getattr(game, "enemies", []) or []:
        name = str(getattr(p, "champion_name", "") or p.champion_alias)
        legendary = [i for i in getattr(p, "items", None) or () if getattr(items.get(int(i)), "kind", "") == "legendary"]
        eff = iz.inventory_effects(legendary)               # boots left out: every pair has pen / resist
        for k in EFFECT_KEYS:
            n = int(eff.get(k, 0))
            if n:
                out.setdefault(EFFECT_FR[k], []).append(name + (f" x{n}" if n >= 2 else ""))
    return out or None


def _ctx_jungle(engine: Any, game: Any, now: float) -> dict[str, Any] | None:
    """The enemy jungler: where / when last seen (minimap tracker), the Tab intel (jungle_intel:
    farming side, recall, dead, level), the probable first-gank side early, the F9 sentence."""
    from treeaicoach import game_plan, geometry

    jg = game.enemy_jungler() if game is not None and hasattr(game, "enemy_jungler") else None
    d: dict[str, Any] = {}
    intel = None                                               # jungle_intel.JungleIntel (Tab data)
    for name in ("jungle_intel", "jungler_intel"):            # engine.jungle_intel(): the state, or a summary
        fn = getattr(engine, name, None)
        if callable(fn):
            v = fn()
            if v is not None and getattr(v, "alias", None) is not None and not isinstance(v, dict):
                intel = v                                      # the raw state: only its useful fields below
            elif isinstance(v, dict):
                d.update(compact(v, max_list=4, max_str=90) or {})
            elif v:
                d["txt"] = str(v)[:90]
            break
    if jg is not None:
        d.setdefault("c", jg.champion_name or jg.champion_alias)
        d.setdefault("lv", int(jg.level))
        if jg.is_dead:
            d["mort"] = int(float(jg.respawn_timer or 0))
        tracker = getattr(engine, "_tracker", None)
        tr = tracker.get(jg.champion_alias) if tracker is not None and hasattr(tracker, "get") else None
        pos = tr.position() if tr is not None and hasattr(tr, "position") else None
        if pos is not None:
            zone = geometry.zone_label_fr(geometry.classify_zone(*pos), getattr(game, "my_team", None))
            d["vu"] = f"{zone}, il y a {int(max(0.0, now - float(tr.last_seen)))} s"
    ji = getattr(engine, "_jungle_intel", None)
    st = intel if intel is not None else (ji.state() if ji is not None and hasattr(ji, "state") else None)
    if st is not None and getattr(st, "alias", None):          # jungle_intel.JungleIntel (Tab data)
        d.setdefault("c", getattr(st, "name", None) or st.alias)
        if getattr(st, "text", None):
            d["txt"] = str(st.text)[:90]
        if getattr(st, "farming", False) and getattr(st, "farm_side", None):
            d["farm"] = st.farm_side
        if getattr(st, "recalled", False):
            d["achat"] = True
        if getattr(st, "level", 0):
            d["lv"] = int(st.level)
    gt = float(getattr(game, "game_time", 0.0) or 0.0)
    if jg is not None and gt < 6 * 60:
        side = game_plan.probable_gank_side(list(getattr(game, "enemies", []) or []))
        if side:
            d["cote_probable"] = side
    if "vu" not in d and "txt" not in d:
        jt = getattr(engine, "jungler_status_text", None)
        text = jt() if callable(jt) else None
        if text:
            d["txt"] = str(text)[:90]
    return d or None


def _ctx_map(engine: Any, game: Any, now: float) -> dict[str, Any] | None:
    """Dragons per team, soul, Baron / Elder buff holder + time left, towers lost per team."""
    tac = getattr(engine, "_tactics", None)
    st = tac.map_state() if tac is not None and hasattr(tac, "map_state") else None
    if st is None:
        return None
    mine = getattr(st, "my_team", None) or getattr(game, "my_team", None)

    def who(team: Any) -> str:
        return "nous" if team == mine else "eux"

    d: dict[str, Any] = {}
    dr = getattr(st, "dragons", None) or {}
    if dr:
        d["dragons"] = f"{int(dr.get(mine, 0))}-{sum(int(v) for k, v in dr.items() if k != mine)}"
    if getattr(st, "soul_team", None):
        d["ame"] = who(st.soul_team)
    if getattr(st, "baron_team", None) and float(getattr(st, "baron_left", 0) or 0) > 0:
        d["baron"] = f"{who(st.baron_team)} {int(st.baron_left)} s"
    if getattr(st, "elder_team", None) and float(getattr(st, "elder_left", 0) or 0) > 0:
        d["ancestral"] = f"{who(st.elder_team)} {int(st.elder_left)} s"
    downs = getattr(st, "turrets_down", None) or ()
    if downs:
        lost_us = sum(1 for t in downs if t[0] == mine)
        d["tours_perdues"] = f"nous {lost_us}, eux {len(downs) - lost_us}"
    inh = getattr(st, "inhibs_down", None) or ()
    if inh:
        d["inhibs_perdus"] = f"nous {sum(1 for t in inh if t[0] == mine)}, eux {sum(1 for t in inh if t[0] != mine)}"
    return d or None


def _ctx_death_causes(engine: Any, game: Any, now: float) -> list[list[Any]] | None:
    """``[[game time, cause line], ...]`` of my last deaths (death_cause.DeathCoach)."""
    plus = getattr(engine, "_coach_plus", None)
    dc = getattr(plus, "deaths", None)
    hist = list(getattr(dc, "log", None) or [])
    return [[int(gt), str(line)[:90]] for gt, _cause, line in hist[-3:]] or None


def _ctx_prio(engine: Any, game: Any, now: float) -> dict[str, str] | None:
    coach = getattr(engine, "_coach", None)
    waves = coach.waves() if coach is not None and hasattr(coach, "waves") else {}
    out = {}
    for lane, w in sorted((waves or {}).items()):
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


def _ctx_extra(engine: Any, game: Any, now: float) -> Any:
    """Analysis another module exposes for the AI (``engine.ai_extra_context() -> dict``, e.g. my
    own resources, the win condition), compacted to a few short fields."""
    fn = getattr(engine, "ai_extra_context", None)
    v = fn() if callable(fn) else None
    return compact(v, max_list=4, max_str=100) if isinstance(v, dict) and v else None


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


def _size(snap: dict[str, Any]) -> int:
    return len(json.dumps(snap, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _fit(snap: dict[str, Any], limit: int) -> dict[str, Any]:
    """Drop the least useful details (in this order) until the JSON fits in ``limit`` bytes."""
    steps: list[Callable[[], None]] = [
        lambda: snap.__setitem__("ev", snap["ev"][-2:]) if "ev" in snap else None,
        lambda: snap.pop("plus", None),
        lambda: snap.__setitem__("coups", snap["coups"][-1:]) if "coups" in snap else None,
        lambda: snap.pop("balises", None),
        lambda: snap.__setitem__("objets_possibles", snap["objets_possibles"][:3])
        if "objets_possibles" in snap else None,
        lambda: snap.pop("prio", None),
        lambda: snap.pop("ev", None),
        lambda: snap.__setitem__("al", [" ".join(x.split()[:4]) for x in snap["al"]]) if "al" in snap else None,
        lambda: snap.pop("coups", None),
        lambda: [snap["compo"].pop(k, None) for k in ("menaces", "fenetre_voie", "plan_eux")]
        if isinstance(snap.get("compo"), dict) else None,
        lambda: snap.get("compo", {}).pop("plan", None) if isinstance(snap.get("compo"), dict) else None,
        lambda: snap.pop("carto", None),
        lambda: snap.pop("conseils", None) if "conseils" in snap else (snap.get("voie") or {}).pop("conseils", None),
        lambda: snap.pop("compo", None),
        lambda: snap.pop("effets", None),
        lambda: snap.pop("objets_possibles", None),
        lambda: snap.pop("jgl", None),
    ]
    for step in steps:
        if _size(snap) <= limit:
            break
        try:
            step()
        except Exception:
            pass
    if _size(snap) > limit:                       # last resort: a hard cut of the extras
        for k in [k for k in list(snap) if k not in ("t", "mo", "carte", "me", "voie", "eq", "en", "obj")][::-1]:
            snap.pop(k, None)
            if _size(snap) <= limit:
                break
    return snap


#: fixed key order of the snapshot (deterministic prompt); unknown context keys come after, sorted
SNAP_ORDER = ("t", "ph", "mo", "mode", "carte", "me", "voie", "eq", "al", "en", "compo", "effets", "jgl", "obj",
              "carto", "morts", "ev", "coups", "prec", "achat", "objets_possibles", "prio", "balises", "wp", "plus")


def build_snapshot(game: Any, *, moment: str = "", roles: Any = None, scoreboard: Any = None,
                   objectives: Iterable[Any] = (), item_text: str | None = None,
                   context: dict[str, Any] | None = None, limit: int = MAX_SNAPSHOT_BYTES,
                   trend: str | None = None) -> dict[str, Any]:
    """THE game state for the AI ("mastermind" snapshot): ONE compact structured dict with
    everything TreeAI knows, in a fixed key order (:data:`SNAP_ORDER`), < ``limit`` bytes
    (~1.3k tokens), no player names. ``context``: :func:`engine_context` (the engine analysis);
    ``trend``: the team gold trend ("-1 200 en 2 min"). Never raises."""
    out: dict[str, Any] = {}
    ctx = dict(context or {})
    try:
        gt = float(getattr(game, "game_time", 0.0) or 0.0)
        out["t"] = _clock(gt)
        if ctx.get("ph"):
            out["ph"] = ctx.pop("ph")
        if moment.startswith("comeback:"):
            reason = moment.split(":", 1)[1]
            out["mo"] = COMEBACK_FR.get(reason, reason)
            out["mode"] = "saisir" if reason in WINDOW_REASONS else "redresser"
        elif moment:
            out["mo"] = MOMENT_FR.get(moment, moment)
        if ctx.get("carte"):
            out["carte"] = ctx.pop("carte")
        me = getattr(game, "me", None)
        if me is not None:
            names, gold = _item_list(me.items)
            mine: dict[str, Any] = {"c": me.champion_name or me.champion_alias}
            try:
                r = roles.my_role() if roles is not None and hasattr(roles, "my_role") else None
            except Exception:
                r = None
            role = ROLE_FR.get(str(r).upper()) if r else _role(me, "ally", None)
            if role:
                mine["r"] = role
            mine.update(lv=int(me.level), k=f"{me.kills}/{me.deaths}/{me.assists}", cs=int(me.creep_score),
                        g=int(getattr(game, "current_gold", 0.0) or 0))
            cs = getattr(game, "champion_stats", None) or {}
            try:
                if float(cs.get("maxHealth") or 0) > 0:
                    mine["pv"] = f"{int(round(100 * float(cs.get('currentHealth', 0)) / float(cs['maxHealth'])))}%"
            except (TypeError, ValueError):
                pass
            if names:
                mine["it"] = names
            if gold:
                mine["ig"] = gold
            if getattr(me, "is_dead", False):
                mine["rs"] = int(max(0.0, float(getattr(me, "respawn_timer", 0.0) or 0.0)))
            out["me"] = mine
        diff = ctx.pop("diff", None) if isinstance(ctx.get("diff"), dict) else None
        tips = ctx.pop("conseils", None)
        if diff is not None and diff.get("vs"):
            voie = {k: diff[k] for k in ("vs", "niv", "po", "cs") if k in diff}
            if tips:
                voie["conseils"] = [str(x)[:110] for x in tips][:2]
            out["voie"] = voie
        eq: dict[str, Any] = {}
        sb_eq = getattr(scoreboard, "team_gold_diff", None) if scoreboard is not None and getattr(
            scoreboard, "players", None) else None
        if diff is not None and "eq" in diff:
            eq["po"] = int(diff["eq"])
        elif sb_eq is not None:
            eq["po"] = int(sb_eq)
        if trend:
            eq["tend"] = trend
        if diff is not None and diff.get("kills"):
            eq["kills"] = diff["kills"]
        elif scoreboard is not None and getattr(scoreboard, "players", None):
            eq["kills"] = f"{scoreboard.ally_kills}-{scoreboard.enemy_kills}"
        allies = list(getattr(game, "allies", []) or [])[:4]
        enemies = list(getattr(game, "enemies", []) or [])[:5]
        if me is not None and enemies:
            eq["niv"] = int(sum(int(p.level) for p in [me] + allies) - sum(int(p.level) for p in enemies))
        if eq:
            out["eq"] = eq
        out["al"] = [_pline(p, "ally", roles) for p in allies]
        out["en"] = [_pline(p, "enemy", roles) for p in enemies]
        for k in ("compo", "effets", "jgl"):
            if ctx.get(k):
                out[k] = ctx.pop(k)
        objt = []
        for o in objectives or ():
            name, rem = getattr(o, "name", ""), getattr(o, "remaining", None)
            if name and (getattr(o, "alive", False) or (rem is not None and rem < 900)):
                objt.append([name, 0 if getattr(o, "alive", False) else int(rem)])
        if objt:
            out["obj"] = sorted(objt, key=lambda x: (x[1], x[0]))[:5]
        if ctx.get("carto"):
            out["carto"] = ctx.pop("carto")
        deaths = last_deaths(game)
        causes = ctx.pop("causes", None) or []
        for d in deaths:
            te = _secs_of(d.get("t"))
            hit = next((c for c in causes if te is not None and abs(int(c[0]) - te) <= 4), None)
            if hit is not None:
                d["cause"] = hit[1]
        if deaths:
            out["morts"] = deaths
        ev = [e for e in recent_events(game, limit=12) if gt - (_secs_of(e.split(" ", 1)[0]) or 0) <= 150]
        if ev:
            out["ev"] = ev[-4:]
        for k in ("coups", "prec"):
            if ctx.get(k) not in (None, "", []):
                out[k] = ctx.pop(k)
        if item_text:
            out["achat"] = str(item_text)[:120]
        try:
            api_role = roles.my_role() if roles is not None and hasattr(roles, "my_role") else None
        except Exception:
            api_role = None
        cands = candidate_items(game, api_role)
        if cands:
            out["objets_possibles"] = cands[:6]
        for k in ("prio", "balises", "wp", "plus"):
            if ctx.get(k) not in (None, "", [], {}):
                out[k] = ctx.pop(k)
        for k in sorted(ctx):                       # unknown sections (other modules), after the known ones
            if ctx[k] not in (None, "", [], {}) and k not in out:
                out[k] = ctx[k]
        out = _fit(out, limit)
    except Exception:
        log.debug("AI snapshot incomplete", exc_info=True)
    return out


def _secs_of(clock_text: Any) -> int | None:
    """"12:04" -> 724 (None when not a clock)."""
    m = re.match(r"^(\d+):(\d\d)$", str(clock_text or "").strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def snapshot_tokens(snap: dict[str, Any]) -> int:
    """Rough token count of a snapshot as sent (French JSON ~ 2.9 bytes / token, measured)."""
    return int(math.ceil(_size(snap) / 2.9))


def build_prompt(snapshot: dict[str, Any]) -> str:
    moment = snapshot.get("mo") or "point de situation"
    data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    mode = ""
    if snapshot.get("mode") == "redresser":
        mode = ("Mode : redresser. Donne UN plan pour revenir dans la partie : où jouer, quoi éviter, quel "
                "objectif échanger. ")
    elif snapshot.get("mode") == "saisir":
        mode = "Mode : fenêtre favorable. Dis quel objectif prendre maintenant et comment. "
    card = snapshot.get("carte")
    rule = ("La carte du coach affiche : « " + str(card.get("appel")) + " ». Ton plan la SUIT (carte=suit) ; "
            "si tu n'es pas d'accord, carte=differe et pourquoi = 1 ligne courte fondée sur le JSON. "
            if isinstance(card, dict) and card.get("appel") else "Pas de carte active : carte=aucune. ")
    return (f"Moment : {moment}.\nÉtat de la partie (JSON, API officielle + analyse de la minimap) : {data}\n{mode}"
            + rule + "Réponds UNIQUEMENT avec un objet JSON strict de cette forme : " + PLAN_SCHEMA_FR + ".")


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
    card: str | None = None   # CARD_STANCES: the plan vs the live card ("suit" / "differe" / "aucune")
    why: str | None = None    # one line: why the plan departs from the card (card == "differe")

    @property
    def urgency(self) -> int:
        """Presenter urgency: 2 for the answer to F8 and the bonus "urgence" plan (shown even to an
        expert, above an ordinary panel line), 1 otherwise."""
        reason = self.moment.split(":", 1)[1] if self.moment.startswith("comeback:") else ""
        return 2 if (self.error or self.moment == "manual" or reason in URGENT_REASONS) else 1

    @property
    def max_age(self) -> float:
        """Seconds after the moment during which this advice may still be shown (a plan for a
        moment that has passed is stale: never displayed late)."""
        if self.error:
            return 30.0
        return MANUAL_MAX_AGE_S if self.moment == "manual" else PLAN_MAX_AGE_S

    def check_card(self, card: Any) -> str:
        """:func:`card_check` of this advice against the live card ("ok" / "explained" / "conflict")."""
        if self.error or self.source == "rules" and self.card == "suit":
            return "ok"
        return card_check(self.text, card, stance=self.card, why=self.why, goal=self.goal)

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


def _dead_enemies(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """``[{"c", "rs"}]`` of the dead enemies of a snapshot (player lines "Zed mid 9 ... mort 23s")."""
    out = []
    for e in snap.get("en") or []:
        if isinstance(e, dict) and "rs" in e:
            out.append({"c": e.get("c"), "rs": int(e.get("rs") or 0)})
        elif isinstance(e, str):
            m = re.search(r"\bmort (\d+)s\b", e)
            if m:
                out.append({"c": e.split(" ", 1)[0], "rs": int(m.group(1))})
    return out


def _rule_plan(moment: str, snap: dict[str, Any]) -> dict[str, Any] | None:
    reason = moment.split(":", 1)[1] if moment.startswith("comeback:") else ""
    genie = snap.get("carte") or snap.get("genie")
    if isinstance(genie, dict) and genie.get("appel"):        # the live game-changer card (macro.py): follow it
        steps = [_short(genie.get("pourquoi"), MAX_STEP_CHARS)] if genie.get("pourquoi") else []
        return {"plan": str(genie["appel"]), "etapes": steps, "objectif": _goal(genie["appel"]),
                "urgence": "haute", "carte": "suit"}
    me = snap.get("me") or {}
    lane = _ROLE_LANE.get(str(me.get("r") or ""))
    prio = (snap.get("prio") or {}).get(lane or "", "")
    wards_ = [str(w) for w in (snap.get("balises") or [])][:2]
    objt = [o for o in (snap.get("obj") or snap.get("objt") or []) if isinstance(o, list) and len(o) == 2]
    nxt = next((o for o in objt if int(o[1]) <= 120), None)
    diff = dict(snap.get("diff") or {})
    diff.update({k: v for k, v in (snap.get("voie") or {}).items() if k == "vs"})
    eq = snap.get("eq") or {}
    gd = int(eq.get("po", diff.get("eq", (snap.get("sb") or {}).get("gd", 0))) or 0)
    dead = _dead_enemies(snap)
    jg = snap.get("jgl")
    jgl = str((jg.get("txt") or (f"{jg.get('c')} vu {jg['vu']}" if jg.get("vu") else "")) if isinstance(jg, dict)
              else (jg or "")).strip()
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
        self.sent = 0                     # requests really sent this game (never refunded: the hard cap)

    @property
    def auto_used(self) -> int:
        return len(self.used)

    @property
    def total(self) -> int:
        return self.auto_used + self.urgent_used + self.manual

    @property
    def exhausted(self) -> bool:
        # refunds give a slot back, never a request: failed / unusable answers still count here
        return max(self.total, self.sent) >= GAME_HARD_CAP

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
        if reason in WINDOW_REASONS:               # a game-changing window (ace, carries dead, numbers, lead)
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
                "total": self.total, "sent": self.sent, "cap": GAME_HARD_CAP}


def budget_text(b: dict[str, Any] | None) -> str:
    """Short French counter for the HUD / dashboard, e.g. ``"IA 3/5"`` (``+1`` once the bonus is used)."""
    if not b:
        return ""
    text = f"IA {int(b.get('auto_used', 0))}/{int(b.get('auto_max', AUTO_BUDGET))}"
    if b.get("urgent_used"):
        text += " +1"
    return text


class AIAdvisor:
    """Rate-limited background LLM advice. Thread-safe; public methods never raise.

    One request at a time (an in-flight flag reserved under the lock: the engine thread and the
    F8 hotkey / UI thread can never start two); an answer from a previous game is discarded
    (generation counter); a key moment during a fight / gank is deferred, not lost (retried up
    to :data:`DEFER_S` after); a failed or unusable answer gives its budget slot back but still
    counts towards :data:`GAME_HARD_CAP`. The advisor also keeps the game's timeline (team gold
    trend, one line every 2 min, the plans given) for the snapshot and the post-game review."""

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
        self._inflight = False
        self._gen = 0
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
        self._deferred: tuple[str, float] | None = None
        self._samples: list[tuple[float, int]] = []      # (game time, team gold diff)
        self.timeline: list[str] = []                    # post-game review: one line / 2 min + plans given
        self._next_line_gt = 0.0
        self.rule_plans = 0                   # offline plans published (rules), this session
        self.json_errors = 0                  # answers that were broken JSON
        self.conflicts = 0                    # AI plans dropped: they contradicted the live card
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
            self._gen += 1                    # an answer still in flight belongs to the last game
            self._result = None
            self._last_call = -math.inf
            self._last_rules = -math.inf
            self._bad_plays = []
            self._deferred = None
            self._samples = []
            self.timeline = []
            self._next_line_gt = 0.0
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
        if self._inflight:
            return True
        th = self._thread
        return th is not None and th.is_alive()

    # ------------------------------------------------------------------ timeline / trend
    def _record(self, gt: float, game: Any, scoreboard: Any) -> None:
        """Team gold samples (trend) and one timeline line every :data:`TIMELINE_EVERY_S`."""
        try:
            if scoreboard is None or not getattr(scoreboard, "players", None):
                return
            gd = int(scoreboard.team_gold_diff)
            with self._lock:
                if not self._samples or gt - self._samples[-1][0] >= 10.0:
                    self._samples.append((gt, gd))
                    self._samples = [x for x in self._samples if gt - x[0] <= TREND_WINDOW_S + 60.0]
                if gt >= self._next_line_gt and gt >= 60.0:
                    self._next_line_gt = (gt // TIMELINE_EVERY_S + 1) * TIMELINE_EVERY_S
                    me = getattr(game, "me", None)
                    mine = f" moi niv {me.level} {me.kills}/{me.deaths}/{me.assists} {me.creep_score} cs" \
                        if me is not None else ""
                    self.timeline.append(f"{_clock(gt)} or {gd:+d} kills {scoreboard.ally_kills}-"
                                         f"{scoreboard.enemy_kills}{mine}")
                    del self.timeline[:-30]
        except Exception:
            log.debug("AI timeline failed", exc_info=True)

    def trend(self, gt: float) -> str | None:
        """Team gold change over the last :data:`TREND_WINDOW_S` ("-1200 en 2 min"), None if unknown."""
        with self._lock:
            old = [v for ts, v in self._samples if gt - ts >= TREND_WINDOW_S - 15.0]
            if not old or not self._samples:
                return None
            delta = self._samples[-1][1] - old[-1]
        return f"{delta:+d} en 2 min"

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
            self._bad_plays = []
        return True

    def update(self, t: float, game: Any, *, in_base: bool = False, objectives: Iterable[Any] = (),
               roles: Any = None, scoreboard: Any = None, item_text: str | None = None,
               threat: int = 0, context: Any = None, win_prob: float | None = None,
               in_fight: bool = False) -> bool:
        """Detect a key moment and maybe start a request. Returns True if one was started.

        ``context``: extra snapshot sections (dict), or a callable returning them (only called
        when a request is really sent), e.g. ``lambda: engine_context(engine)``. Never during a
        fight or a gank threat: the moment is deferred (:data:`DEFER_S`). When the provider is
        unreachable (back-off after an error), a plan moment gets an offline :func:`rule_plan`
        instead (``Advice.source == "rules"``, no budget used)."""
        try:
            objectives = list(objectives or ())
            gt = float(getattr(game, "game_time", 0.0) or 0.0)
            self._record(gt, game, scoreboard)
            moment = self.detector.update(game, in_base, objectives)
            reason = self.comeback.update(t, game, scoreboard, win_prob, objectives)
            if moment is None and reason is not None:
                moment = f"comeback:{reason}"
            if moment is None and self._blunder_moment(t):
                moment = "comeback:blunders"
            if not self.enabled:
                return False
            busy_now = threat >= 1 or in_fight
            if moment is None:
                d = self._deferred
                if d is None or busy_now:
                    return False
                if t - d[1] > DEFER_S:
                    self._deferred = None
                    return False
                moment = d[0]                  # the fight is over: the deferred moment now
            if busy_now:
                if self._deferred is None or self._deferred[0] != moment:
                    self._deferred = (moment, t)
                return False
            self._deferred = None
            if moment == "base" and getattr(getattr(game, "me", None), "is_dead", False):
                return False
            with self._lock:
                slot = self.budget.pick(moment, gt, self.detector.last_objective)
                if slot is None or (self._urgent_only and slot != "urgent"):
                    return False
                interval = URGENT_MIN_INTERVAL_S if slot == "urgent" else MIN_INTERVAL_S
                if gt < MIN_GAME_TIME_S or t - self._last_call < interval:
                    return False
                offline = t < self._blocked_until
                if not offline and (self._inflight or (self._thread is not None and self._thread.is_alive())):
                    return False
                if not offline:
                    self._last_call = t
                    self.budget.take(slot)
                    self.budget.sent += 1
                    self._inflight = True       # reserved: no second request can start meanwhile
                gen = self._gen
            try:
                snap = build_snapshot(game, moment=moment, roles=roles, scoreboard=scoreboard,
                                      objectives=objectives, item_text=item_text, context=_resolve(context),
                                      trend=self.trend(gt))
                fallback = rule_plan(moment, snap) if is_plan_moment(moment) else None
            except Exception:
                if not offline:
                    with self._lock:
                        self._inflight = False
                        self.budget.refund(slot)
                raise
            if offline:
                self._offline_plan(fallback, moment, t)
                return False
            self._start(build_prompt(snap), moment, t, self._validator(game, snap, item_text), slot=slot,
                        fallback=fallback, gen=gen)
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
                                  source="rules", goal=plan.get("objectif"), card=plan.get("carte"))
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
                if self._inflight or (self._thread is not None and self._thread.is_alive()):
                    return "L'IA réfléchit déjà…"
                wait = MANUAL_MIN_INTERVAL_S - (t - self._last_call)
                if wait > 0:
                    return f"Patiente encore {int(math.ceil(wait))} s avant de redemander."
                if self._blocked_until == math.inf and self._status:
                    return self._status
                if t < self._blocked_until and self._status:
                    return f"{self._status} ({int(math.ceil(self._blocked_until - t))} s)"
                if self.budget.exhausted:
                    return f"Limite de {GAME_HARD_CAP} questions IA atteinte pour cette partie : le coach continue sans IA."
                self._last_call = t
                self.budget.manual += 1
                self.budget.sent += 1
                self._inflight = True
                gen = self._gen
            gt = float(getattr(game, "game_time", 0.0) or 0.0)
            try:
                snap = build_snapshot(game, moment="manual", roles=roles, scoreboard=scoreboard,
                                      objectives=list(objectives or ()), item_text=item_text,
                                      context=_resolve(context), trend=self.trend(gt))
            except Exception:
                with self._lock:
                    self._inflight = False
                raise
            self._start(build_prompt(snap), "manual", t, self._validator(game, snap, item_text), gen=gen)
            return "Question envoyée à l'IA…"
        except Exception:
            log.exception("AIAdvisor.ask failed")
            return "Conseil IA indisponible."

    def poll(self) -> Advice | None:
        """The finished advice (once), else None."""
        with self._lock:
            res, self._result = self._result, None
            return res

    def note_shown(self, adv: Advice, gt: float | None = None) -> None:
        """The engine displayed ``adv``: kept in the timeline for the post-game review."""
        try:
            if adv is None or adv.error:
                return
            with self._lock:
                when = _clock(gt) if gt is not None else ""
                who = "plan IA" if adv.source == "ai" else "plan règles"
                self.timeline.append(f"{when} {who} : {adv.text[:140]}".strip())
                del self.timeline[:-30]
        except Exception:
            log.debug("note_shown failed", exc_info=True)

    def note_dropped(self, adv: Advice, why: str) -> None:
        """The engine did NOT show ``adv`` (stale / contradicted the card)."""
        log.info("AI advice not shown (%s): %s", why, getattr(adv, "text", "")[:100])
        if why == "conflict":
            with self._lock:
                self.conflicts += 1

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
               fallback: dict[str, Any] | None = None, gen: int | None = None) -> None:
        prov, key, model = self._provider, self._key, self._model
        url = self._urls.get(prov)
        kw: dict[str, Any] = {"timeout": TIMEOUT_S, "url": url}
        if _accepts(self._caller, "json_mode") and _accepts(self._caller, "max_tokens"):   # (may be swapped)
            kw.update(json_mode=True, max_tokens=PLAN_MAX_TOKENS)
        gen = self._gen if gen is None else gen
        with self._lock:
            self._inflight = True

        def job() -> None:
            try:
                raw = self._caller(prov, key, model, system_prompt(), prompt, **kw)
                plan = parse_plan(raw) if "{" in str(raw or "") else None
                steps: tuple[str, ...] = ()
                goal = card = why = None
                if plan is not None:
                    text: str | None = plan_text(plan)
                    steps, goal = tuple(plan["etapes"]), plan.get("objectif")
                    card, why = plan.get("carte"), plan.get("pourquoi")
                elif "{" in str(raw or ""):
                    text = None                      # broken JSON: not shown as is
                    with self._lock:
                        self.json_errors += 1
                else:
                    text = clean_advice(raw)         # a plain answer (model ignored the format)
                if text and validate is not None:
                    checked = validate(text)
                    if checked != text:
                        steps, goal, card, why = (), None, None, None
                    text = checked
                with self._lock:
                    if gen != self._gen:
                        return                       # a new game started meanwhile: never shown
                    self.calls += 1
                    if text:
                        self._result = Advice(text, moment, t, steps=steps, goal=goal, card=card, why=why)
                        return
                    if slot:
                        self.budget.refund(slot)     # nothing usable: the slot stays for a later moment
                    if moment == "manual":
                        self._result = Advice("L'IA n'a pas donné de conseil fiable cette fois.", moment, t,
                                              error=True)
                    elif fallback:
                        self._result = Advice(plan_text(fallback), moment, t, steps=tuple(fallback["etapes"]),
                                              source="rules", goal=fallback.get("objectif"),
                                              card=fallback.get("carte"))
                        self.rule_plans += 1
            except AIError as exc:
                self._fail(exc.code, prov, moment, t, slot, fallback, gen=gen, retry_after=exc.retry_after)
            except Exception:
                log.exception("AI request failed")
                self._fail("server", prov, moment, t, slot, fallback, gen=gen)
            finally:
                with self._lock:
                    if gen == self._gen or self._thread is threading.current_thread():
                        self._inflight = False

        th = threading.Thread(target=job, name="TreeAICoach-ai", daemon=True)
        with self._lock:
            self._thread = th
        th.start()

    def _fail(self, code: str, provider: str, moment: str = "", t: float = 0.0,
              slot: str | None = None, fallback: dict[str, Any] | None = None, gen: int | None = None,
              retry_after: float | None = None) -> None:
        log.info("AI advice unavailable (%s, %s)", provider, code)
        with self._lock:
            same_game = gen is None or gen == self._gen
            if slot and same_game:
                self.budget.refund(slot)          # no answer: the slot stays available
            text = error_text(code, provider)
            if moment == "manual" and same_game:
                self._result = Advice(text, moment, t, error=True)
            if text != self._status:
                self._status_seq += 1
            self._status = text
            wait = BACKOFF_S.get(code, 180.0)
            if code == "rate" and retry_after is not None:
                wait = min(RATE_BACKOFF_MAX_S, max(RATE_BACKOFF_MIN_S, float(retry_after) + 2.0))
            if self._blocked_until != math.inf:   # never shortens a block set for the game (self-check)
                self._blocked_until = self._clock() + wait
        if moment != "manual" and same_game:
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
    "Tu es un coach expert de League of Legends. Voici le rapport de la partie que le joueur vient de "
    "terminer. Écris UNIQUEMENT en français (tutoiement, aucun mot anglais, pas de markdown, pas d'emoji) : "
    "2 points forts, puis EXACTEMENT 3 axes de progrès ; chaque axe commence par un verbe à l'impératif, "
    "cite le chiffre exact du rapport qui le prouve (morts, CS/min, vision, avance des alertes...) et "
    "donne un exercice précis et mesurable pour la prochaine partie. Respecte le classement des morts du "
    "rapport (« alerte ignorée », « alerte trop tardive », « 1v1 perdu », « l'app n'a pas prévenu ») : ne "
    "reproche pas au joueur une alerte arrivée trop tard. OBJETS : nomme seulement des objets de la liste "
    "« objets autorisés », écrits exactement comme dans la liste ; un objet légendaire ne s'achète jamais "
    "avant le premier retour en base. Pas de spéculation sur les temps de recharge."
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
            # a decimal point ("0.44/min", "5.2 CS/min") does not end a sentence
            for sent in re.findall(r"(?:[^.!?]|(?<=\d)\.(?=\d))+[.!?]*", para):
                ids = items_mentioned(sent)
                # consumables / trinkets / starters are always fine (a control ward is not a build)
                bad = [i for i in ids if (i >= 0 and i not in ok and getattr(items.get(i), "kind", "") not in (
                    "consumable", "trinket", "starter")) or (i < 0 and not (_AMBIG.get(i, frozenset()) & ok))]
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
    """The key numbers the review must cite (plain French, verdict labels of analysis.VERDICT_FR)."""
    a = analysis if isinstance(analysis, dict) else {}
    s = a.get("summary") if isinstance(a.get("summary"), dict) else {}
    parts = [f"{s.get('kills', 0)}/{s.get('deaths', 0)}/{s.get('assists', 0)}"]
    if s.get("cs_per_min") is not None:
        parts.append(f"{s.get('cs_per_min')} CS/min")
    if s.get("vision_per_min") is not None:
        parts.append(f"vision {s.get('vision_per_min')}/min")
    if s.get("kill_participation") is not None:
        try:
            parts.append(f"participation {int(round(100 * float(s['kill_participation'])))} %")
        except (TypeError, ValueError):
            pass
    v = a.get("death_verdicts") if isinstance(a.get("death_verdicts"), dict) else {}
    if v:
        try:
            from treeaicoach.analysis import VERDICT_FR
        except Exception:  # pragma: no cover
            VERDICT_FR = {}
        parts.append("morts : " + ", ".join(f"{n} {VERDICT_FR.get(k, k)}" for k, n in v.items() if n))
    lead = a.get("alert_lead") if isinstance(a.get("alert_lead"), dict) else {}
    if lead.get("n"):
        parts.append(f"alertes {lead.get('mean')} s avant les morts en moyenne")
    return " ; ".join(parts)


def _looks_english(text: str) -> bool:
    return len(_EN_WORDS.findall(text or "")) > REVIEW_MAX_EN_WORDS


def _rule_axes(analysis: Any) -> list[dict[str, str]]:
    """Fallback progress axes from the report numbers (when the model gave fewer than 3 usable)."""
    a = analysis if isinstance(analysis, dict) else {}
    s = a.get("summary") if isinstance(a.get("summary"), dict) else {}
    v = a.get("death_verdicts") if isinstance(a.get("death_verdicts"), dict) else {}
    out: list[dict[str, str]] = []
    deaths = int(s.get("deaths") or 0)
    if v.get("ignored"):
        out.append({"axe": "Recule dès l'alerte", "preuve": f"{v['ignored']} morts après une alerte ignorée",
                    "exercice": "Partie suivante : à chaque alerte, fais 3 pas vers ta tour avant de regarder."})
    if deaths >= 5:
        out.append({"axe": "Meurs moins", "preuve": f"{deaths} morts",
                    "exercice": "Fixe-toi 4 morts maximum ; après chaque mort, dis à voix haute qui t'a tué."})
    try:
        cs = float(s.get("cs_per_min") or 0)
    except (TypeError, ValueError):
        cs = 0.0
    if 0 < cs < 7.0 and str(s.get("position") or "") not in ("UTILITY", "JUNGLE"):
        out.append({"axe": "Farme mieux", "preuve": f"{cs} CS/min",
                    "exercice": "Mode entraînement 10 min : 80 sbires sans objets, puis vise 7 CS/min en partie."})
    try:
        vis = float(s.get("vision_per_min") or 0)
    except (TypeError, ValueError):
        vis = 0.0
    if 0 < vis < 0.8:
        out.append({"axe": "Pose plus de balises", "preuve": f"vision {vis}/min",
                    "exercice": "Achète une balise de contrôle à chaque retour et pose ta balise à 2:45."})
    out.append({"axe": "Joue autour des objectifs", "preuve": "chaque dragon et Baron",
                "exercice": "60 s avant chaque objectif : pousse ta vague puis rejoins la rivière."})
    return out


REVIEW_SCHEMA = ('{"forces": ["point fort 1 avec un chiffre", "point fort 2"], "axes": [{"axe": "verbe à '
                 'l\'impératif, 60 caractères max", "preuve": "le chiffre exact de l\'analyse", "exercice": '
                 '"un exercice précis et mesurable pour la prochaine partie"}, {...}, {...}], '
                 '"objets": "1 phrase sur les objets (noms de la liste autorisée uniquement) ou vide"}')


def parse_review(text: Any) -> dict[str, Any] | None:
    """The review JSON (``forces`` <= 2, ``axes`` <= 3 with ``axe`` / ``preuve`` / ``exercice``,
    ``objets``) or None. Never raises."""
    try:
        block = _json_block(text)
        data = json.loads(block) if block else None
        if not isinstance(data, dict):
            return None
        forces = [_short(x, 200) for x in data.get("forces") or [] if isinstance(x, str) and x.strip()][:2]
        axes = []
        for ax in data.get("axes") or []:
            if isinstance(ax, dict) and isinstance(ax.get("axe"), str) and isinstance(ax.get("exercice"), str):
                axes.append({"axe": _short(ax["axe"], 90), "preuve": _short(ax.get("preuve") or "", 120),
                             "exercice": _short(ax["exercice"], 220)})
        objets = _short(data.get("objets"), 240) if isinstance(data.get("objets"), str) else ""
        if not axes and not forces:
            return None
        return {"forces": forces, "axes": axes[:3], "objets": objets}
    except (ValueError, TypeError):
        return None


def render_review(rev: dict[str, Any]) -> str:
    """Plain French text of a parsed review: strengths, 3 numbered axes with their drill, items."""
    def dot(x: str) -> str:
        x = x.strip()
        return x if not x or x.endswith((".", "!", "?", "…")) else x + "."

    lines = []
    if rev.get("forces"):
        lines.append("Points forts : " + " ".join(dot(f) for f in rev["forces"]))
    for i, ax in enumerate(rev.get("axes") or [], 1):
        proof = f" ({ax['preuve'].rstrip('.')})" if ax.get("preuve") else ""
        lines.append(f"{i}. {ax['axe'].rstrip('.')}{proof}. Exercice : {dot(ax['exercice'])}")
    if rev.get("objets"):
        lines.append("Objets : " + dot(rev["objets"]))
    return "\n".join(lines)


def _ground_parsed(rev: dict[str, Any], cands: list[dict[str, Any]], build: list[str],
                   analysis: Any) -> dict[str, Any]:
    """Item grounding per field: a strength / axis / drill naming an item outside the allowed list
    loses that sentence (an axis left empty is replaced from :func:`_rule_axes`), the items line is
    replaced by a grounded one; English names of allowed items become French. Always 3 axes."""
    def g(text: str) -> str:
        out, _n = ground_review(text, cands)
        return out.strip()

    forces = [x for x in (g(f) for f in rev.get("forces") or []) if x]
    axes = []
    for ax in rev.get("axes") or []:
        a2 = {"axe": g(ax["axe"]), "preuve": g(ax.get("preuve") or ""), "exercice": g(ax["exercice"])}
        if a2["axe"] and a2["exercice"]:
            axes.append(a2)
    seen = {_fold(a["axe"])[:12] for a in axes}
    for extra in _rule_axes(analysis):
        if len(axes) >= 3:
            break
        if _fold(extra["axe"])[:12] not in seen:
            axes.append(extra)
    objets = rev.get("objets") or ""
    if objets and cands:
        grounded, removed = ground_review(objets, cands)
        objets = grounded.strip() if not removed else ""
    if not objets and cands:
        keep = next((c for c in cands if c["n"] in build), None)
        aim = next((c for c in cands if c["n"] not in build), None)
        if keep and aim:
            objets = f"Garde {keep['n']} et vise {aim['n']} ({aim['pourquoi']})"
        elif aim:
            objets = f"Vise {aim['n']} ({aim['pourquoi']})"
    return {"forces": forces, "axes": axes[:3], "objets": objets}


def postgame_review(cfg: Any, analysis: Any, *, caller: Callable[..., Any] = call_llm,
                    url: str | None = None, timeline: Iterable[str] = ()) -> str | None:
    """Blocking AI review of a finished game (None if no provider / on error). ONE request.

    Grounded like the live advisor: the prompt carries the report (compacted analysis), the key
    numbers to cite, the game's timeline (team gold / kills every 2 min + the plans given live,
    :attr:`AIAdvisor.timeline`), my final build and the allowed items (French names). The model
    answers a JSON review (2 strengths, exactly 3 progress axes, each with the number that proves
    it and a drill, one items line), rendered in French; every sentence naming an item outside the
    list is dropped (an off-meta / impossible / English item never reaches the report), English
    names of allowed items become French, an English answer is rejected, a missing axis is filled
    from the report numbers. A provider that ignores the JSON format: the plain text is grounded
    the same way. Never raises."""
    prov = str(getattr(cfg, "ai_provider", "off") or "off").lower()
    if provider_spec(prov) is None:
        return None
    try:
        cands, build = review_item_candidates(analysis)
        data = json.dumps(compact_analysis(analysis, limit=MAX_ANALYSIS_BYTES - 1500), ensure_ascii=False,
                          separators=(",", ":"))
        names = ", ".join(c["n"] for c in cands)
        tl = [str(x)[:160] for x in timeline or ()][-16:]
        prompt = (f"Analyse de la partie (JSON) : {data}\n"
                  + (f"Déroulé (écart d'or d'équipe, kills, plans donnés en direct) : {' | '.join(tl)}\n" if tl else "")
                  + f"Build final du joueur : {', '.join(build) or 'inconnu'}.\n"
                  f"Objets autorisés (noms exacts) : {names or 'aucun : ne conseille aucun objet'}.\n"
                  f"Chiffres à citer : {_review_numbers(analysis)}.\n"
                  f"Réponds UNIQUEMENT avec un objet JSON de cette forme : {REVIEW_SCHEMA}")
        kw: dict[str, Any] = {"timeout": REVIEW_TIMEOUT_S, "url": url, "max_tokens": REVIEW_MAX_TOKENS,
                              "long": True}
        if _accepts(caller, "json_mode"):
            kw["json_mode"] = True
        args = (prov, str(getattr(cfg, "ai_api_key", "") or ""), str(getattr(cfg, "ai_model", "") or ""),
                f"{REVIEW_PROMPT} {SEASON_RULES}", prompt)
        try:
            raw = caller(*args, **kw) or None
        except AIError as exc:
            # after the game nothing is urgent: a per-minute limit (the live plans just used the
            # minute's tokens) is waited out once
            if exc.code != "rate" or (exc.retry_after or 0.0) > REVIEW_RATE_WAIT_S:
                raise
            time.sleep(max(1.0, float(exc.retry_after or 5.0) + 1.0))
            raw = caller(*args, **kw) or None
        if not raw:
            return None
        rev = parse_review(raw)
        text = render_review(_ground_parsed(rev, cands, build, analysis)) if rev is not None else clean_review(raw)
        text = frenchify(text)
        if not text or _looks_english(text):
            log.info("AI post-game review rejected: empty or not in French")
            return None
        if rev is None and cands:
            text, removed = ground_review(text, cands)
            if removed and len(cands) >= 2:
                keep = next((c for c in cands if c["n"] in build), cands[0])
                aim = next((c for c in cands if c["n"] not in build), cands[1])
                text = (text + "\n" if text else "") + (
                    f"Objets : garde {keep['n']} et vise {aim['n']} ({aim['pourquoi']}).")
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
    """Blocking "Tester" button helper: ``(ok, French message)``. Never raises.

    Sends the SAME kind of request as a live plan (system prompt + JSON mode + a small snapshot),
    so a model that cannot do JSON plans fails here, not in game. A per-minute limit (429) still
    proves the key works."""
    prov = str(getattr(cfg, "ai_provider", "off") or "off").lower()
    spec = provider_spec(prov)
    if spec is None:
        return False, "Choisis d'abord un fournisseur d'IA."
    snap = {"t": "8:30", "ph": "voie", "mo": "test de connexion : retour en base avec de l'or à dépenser",
            "me": {"c": "Garen", "r": "top", "lv": 7, "k": "1/0/0", "cs": 62, "g": 1300},
            "obj": [["Dragon", 75]]}
    kw: dict[str, Any] = {"timeout": TIMEOUT_S + 4.0, "url": url}
    json_ok = _accepts(caller, "json_mode") and _accepts(caller, "max_tokens")
    if json_ok:
        kw.update(json_mode=True, max_tokens=PLAN_MAX_TOKENS)
    try:
        raw = caller(prov, str(getattr(cfg, "ai_api_key", "") or ""), str(getattr(cfg, "ai_model", "") or ""),
                     system_prompt(), build_prompt(snap), **kw)
        plan = parse_plan(raw) if "{" in str(raw or "") else None
        if plan is None and json_ok and "{" in str(raw or ""):
            return False, (f"{spec.label} répond, mais pas au format attendu : essaie un autre modèle "
                           f"(par défaut : {spec.default_model}).")
        text = plan_text(plan) if plan is not None else clean_advice(raw)
        return True, f"Connexion OK ({spec.label}) : {text}"
    except AIError as exc:
        if exc.code == "rate":
            return True, f"Connexion OK ({spec.label}) : clé valide, limite par minute atteinte pour l'instant."
        return False, error_text(exc.code, prov)
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("AI test failed")
        return False, f"Conseil IA : erreur inattendue ({type(exc).__name__})."
