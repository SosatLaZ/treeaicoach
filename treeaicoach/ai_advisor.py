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
one request every :data:`MIN_INTERVAL_S`, one request at a time in a daemon thread with a
:data:`TIMEOUT_S` timeout (``urllib`` only). The game tick never waits: the engine polls
:meth:`AIAdvisor.poll` for a finished answer. Errors become one French status message
(:data:`ERROR_FR`) and a back-off; nothing here ever raises into the caller.

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
from typing import Any

log = logging.getLogger(__name__)

TIMEOUT_S = 6.0
MIN_INTERVAL_S = 90.0
MIN_GAME_TIME_S = 90.0
MAX_RESPONSE_BYTES = 256 * 1024
MAX_OUTPUT_TOKENS = 200
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
OBJECTIVE_LEAD_S = 60.0
OBJECTIVE_WINDOW_S = 8.0        # announced if the remaining time is within 60 s +/- this
#: back-off after an error (seconds before the next automatic request)
BACKOFF_S: dict[str, float] = {"key": math.inf, "nokey": math.inf, "quota": 600.0, "offline": 300.0,
                               "model": math.inf, "server": 180.0, "empty": 90.0, "bad": 180.0}
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


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
    "Réponds en français, en 2 phrases courtes max, conseils concrets d'achat et de macro, "
    "pas de spéculation sur les temps de recharge ennemis. Pas de liste, pas de markdown, "
    "pas d'introduction : seulement le conseil."
)
MOMENT_FR = {
    "base": "retour en base avec de l'or à dépenser",
    "death": "je viens de mourir",
    "level": "je viens de passer un niveau clé",
    "objective": "objectif neutre dans 60 secondes",
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
                  url: str | None = None, max_tokens: int = MAX_OUTPUT_TOKENS) -> tuple[str, dict[str, str], bytes]:
    """``(url, headers, json body)`` for one chat request. Raises :class:`AIError` ("nokey")."""
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
    elif provider == "ollama":
        target = url or spec.url
        body = {"model": model, "stream": False, "options": {"num_predict": max_tokens, "temperature": 0.4},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    else:  # anthropic
        target = url or spec.url
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
        body = {"model": model, "max_tokens": max_tokens, "system": system,
                "messages": [{"role": "user", "content": prompt}]}
    return target, headers, json.dumps(body, ensure_ascii=False).encode("utf-8")


def parse_response(provider: str, data: Any, long: bool = False) -> str:
    """Text of a provider's JSON answer. Raises :class:`AIError` ("empty" / "bad")."""
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
             long: bool = False) -> str:
    """One blocking request; returns the cleaned advice. Raises :class:`AIError` only.

    OpenAI-compatible providers: when the model does not exist for this key, the available
    models are listed once and the best chat model is picked (and remembered) automatically."""
    model = (model or "").strip() or _auto_model.get(provider, "")
    try:
        return _call_once(provider, api_key, model, system, prompt, timeout, url, max_tokens, long)
    except AIError as exc:
        if exc.code != "model" or provider not in ("groq", "openrouter"):
            raise
        best = pick_model(provider, list_models(provider, api_key, url, timeout))
        if not best or best == model:
            raise
        log.info("AI model %r unavailable, using %r", model or "default", best)
        _auto_model[provider] = best
        return _call_once(provider, api_key, best, system, prompt, timeout, url, max_tokens, long)


def _call_once(provider: str, api_key: str, model: str, system: str, prompt: str, timeout: float,
               url: str | None, max_tokens: int, long: bool) -> str:
    target, headers, body = build_request(provider, model, api_key, system, prompt, url, max_tokens)
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
    return parse_response(provider, data, long)


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
          "achat=suggestion d'objet actuelle, ward=balise conseillée.")
MAX_SNAPSHOT_BYTES = 6000
STAT_KEYS = {"attackDamage": "ad", "abilityPower": "ap", "armor": "ar", "magicResist": "mr",
             "attackSpeed": "as", "moveSpeed": "ms", "abilityHaste": "ah", "critChance": "crit",
             "lifeSteal": "vol", "physicalLethality": "leta", "magicPenetrationFlat": "penm"}
_DROP_KEYS = frozenset({"icon", "me_icon", "skin", "skin_id", "image", "frame", "minimap_bgr", "raw",
                        "series", "samples", "points", "path", "spots", "heatmap", "riot_id",
                        "summoner_name", "name_raw"})


def _clock(gt: Any) -> str:
    try:
        s = max(0, int(float(gt)))
    except (TypeError, ValueError, OverflowError):
        s = 0
    return f"{s // 60}:{s % 60:02d}"


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
    return compact(ctx) or {}


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
        if moment:
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
    return (f"Moment : {moment}.\nÉtat de la partie (JSON, API officielle + analyse de la minimap) : {data}\n"
            "Réponds en 2 phrases courtes max, conseils concrets d'achat et de macro, "
            "pas de spéculation sur les temps de recharge ennemis. Pour un achat, choisis UNIQUEMENT parmi "
            "objets_possibles (noms exacts) ou les composants de la suggestion « achat » ; ne conseille "
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
            if key not in ("dragon", "baron", "elder") or getattr(o, "alive", False) or rem is None:
                continue
            sig = (key, round(float(getattr(o, "next_spawn", 0.0) or 0.0)))
            if abs(float(rem) - OBJECTIVE_LEAD_S) <= OBJECTIVE_WINDOW_S and sig not in self._obj_done:
                self._obj_done.add(sig)
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
        self.detector = MomentDetector()
        self.apply_config(cfg)

    # ------------------------------------------------------------------ config / state
    def apply_config(self, cfg: Any) -> None:
        try:
            prov = str(getattr(cfg, "ai_provider", "off") or "off").lower()
            key = str(getattr(cfg, "ai_api_key", "") or "").strip()
            model = str(getattr(cfg, "ai_model", "") or "").strip()
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
        self.detector.reset()

    def status(self) -> tuple[int, str | None]:
        """``(sequence, French status)``: the sequence changes each time a new error is set."""
        with self._lock:
            return self._status_seq, self._status

    def busy(self) -> bool:
        th = self._thread
        return th is not None and th.is_alive()

    # ------------------------------------------------------------------ live use
    def update(self, t: float, game: Any, *, in_base: bool = False, objectives: Iterable[Any] = (),
               roles: Any = None, scoreboard: Any = None, item_text: str | None = None,
               threat: int = 0, context: Any = None) -> bool:
        """Detect a key moment and maybe start a request. Returns True if one was started.

        ``context``: extra snapshot sections (dict), or a callable returning them (only called
        when a request is really sent), e.g. ``lambda: engine_context(engine)``."""
        try:
            objectives = list(objectives or ())
            moment = self.detector.update(game, in_base, objectives)
            if moment is None or not self.enabled or threat >= 1:
                return False
            gt = float(getattr(game, "game_time", 0.0) or 0.0)
            with self._lock:
                if gt < MIN_GAME_TIME_S or t - self._last_call < MIN_INTERVAL_S or t < self._blocked_until:
                    return False
                if self._thread is not None and self._thread.is_alive():
                    return False
                self._last_call = t
            snap = build_snapshot(game, moment=moment, roles=roles, scoreboard=scoreboard,
                                  objectives=objectives, item_text=item_text, context=_resolve(context))
            self._start(build_prompt(snap), moment, t, self._validator(game, snap, item_text))
            return True
        except Exception:
            log.exception("AIAdvisor.update failed")
            return False

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
                self._last_call = t
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
               validate: Callable[[str], str | None] | None = None) -> None:
        prov, key, model = self._provider, self._key, self._model
        url = self._urls.get(prov)

        def job() -> None:
            try:
                text = self._caller(prov, key, model, system_prompt(), prompt, timeout=TIMEOUT_S, url=url)
                if validate is not None:
                    text = validate(text)
                with self._lock:
                    self.calls += 1
                    if text:
                        self._result = Advice(text, moment, t)
                    elif moment == "manual":
                        self._result = Advice("L'IA n'a pas donné de conseil fiable cette fois.", moment, t,
                                              error=True)
            except AIError as exc:
                self._fail(exc.code, prov, moment, t)
            except Exception:
                log.exception("AI request failed")
                self._fail("server", prov, moment, t)

        th = threading.Thread(target=job, name="TreeAICoach-ai", daemon=True)
        self._thread = th
        th.start()

    def _fail(self, code: str, provider: str, moment: str = "", t: float = 0.0) -> None:
        log.info("AI advice unavailable (%s, %s)", provider, code)
        with self._lock:
            text = error_text(code, provider)
            if moment == "manual":
                self._result = Advice(text, moment, t, error=True)
            if text != self._status:
                self._status_seq += 1
            self._status = text
            self._blocked_until = self._clock() + BACKOFF_S.get(code, 180.0)

    def wait(self, timeout: float = 10.0) -> bool:
        th = self._thread
        if th is not None:
            th.join(timeout)
        return not self.busy()


def _resolve(context: Any) -> dict[str, Any] | None:
    try:
        ctx = context() if callable(context) else context
        return ctx if isinstance(ctx, dict) else None
    except Exception:
        log.debug("AI context unavailable", exc_info=True)
        return None


def system_prompt() -> str:
    return f"{SYSTEM_PROMPT} {LEGEND}"


# ======================================================================================
# Post-game review
# ======================================================================================
REVIEW_PROMPT = (
    "Tu es un coach expert de League of Legends. Voici l'analyse complète (JSON) de la partie que "
    "le joueur vient de terminer. Écris en français une revue d'après-partie concrète : 2 points forts, "
    "3 axes de progrès prioritaires avec un exercice précis pour chacun, et un conseil d'objets ou de "
    "macro pour la prochaine partie avec ce champion. 8 phrases maximum, tutoiement, pas de markdown, "
    "pas de spéculation sur les temps de recharge."
)


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


def postgame_review(cfg: Any, analysis: Any, *, caller: Callable[..., Any] = call_llm,
                    url: str | None = None) -> str | None:
    """Blocking AI review of a finished game (None if no provider / on error). Never raises."""
    prov = str(getattr(cfg, "ai_provider", "off") or "off").lower()
    if provider_spec(prov) is None:
        return None
    try:
        data = json.dumps(compact_analysis(analysis), ensure_ascii=False, separators=(",", ":"))
        return caller(prov, str(getattr(cfg, "ai_api_key", "") or ""), str(getattr(cfg, "ai_model", "") or ""),
                      REVIEW_PROMPT, f"Analyse de la partie (JSON) : {data}", timeout=REVIEW_TIMEOUT_S,
                      url=url, max_tokens=REVIEW_MAX_TOKENS, long=True) or None
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
