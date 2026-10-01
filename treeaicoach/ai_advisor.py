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

import json
import logging
import math
import re
import socket
import threading
import time
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
                     "llama-3.3-70b-versatile", True, "https://console.groq.com/keys"),
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
                  url: str | None = None) -> tuple[str, dict[str, str], bytes]:
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
            "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS, "temperature": 0.4},
        }
    elif provider in ("groq", "openrouter"):
        target = url or spec.url
        headers["Authorization"] = f"Bearer {key}"
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/treeaicoach"
            headers["X-Title"] = "TreeAI Coach"
        body = {"model": model, "max_tokens": MAX_OUTPUT_TOKENS, "temperature": 0.4,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    elif provider == "ollama":
        target = url or spec.url
        body = {"model": model, "stream": False, "options": {"num_predict": MAX_OUTPUT_TOKENS, "temperature": 0.4},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    else:  # anthropic
        target = url or spec.url
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
        body = {"model": model, "max_tokens": MAX_OUTPUT_TOKENS, "system": system,
                "messages": [{"role": "user", "content": prompt}]}
    return target, headers, json.dumps(body, ensure_ascii=False).encode("utf-8")


def parse_response(provider: str, data: Any) -> str:
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
    text = clean_advice(text)
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


def _classify_http(code: int, body: str) -> str:
    low = body.lower()
    if code in (401, 403) or (code == 400 and ("api key" in low or "api_key" in low or "apikey" in low)):
        return "key"
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


def call_llm(provider: str, api_key: str, model: str, system: str, prompt: str,
             timeout: float = TIMEOUT_S, url: str | None = None) -> str:
    """One blocking request; returns the cleaned advice. Raises :class:`AIError` only."""
    target, headers, body = build_request(provider, model, api_key, system, prompt, url)
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
    return parse_response(provider, data)


# ======================================================================================
# Game snapshot + prompt
# ======================================================================================
def _clock(gt: Any) -> str:
    try:
        s = max(0, int(float(gt)))
    except (TypeError, ValueError, OverflowError):
        s = 0
    return f"{s // 60}:{s % 60:02d}"


def _item_names(items: Iterable[Any]) -> list[str]:
    try:
        from treeaicoach.scoreboard import item_info
    except Exception:  # pragma: no cover
        return []
    out = []
    for it in items or ():
        info = item_info(it)
        if info is not None and info[0] and info[2] not in ("trinket",):
            out.append(info[0])
    return out[:7]


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
    d: dict[str, Any] = {"champion": p.champion_name or p.champion_alias}
    role = _role(p, side, roles)
    if role:
        d["role"] = role
    d.update({"niveau": int(p.level), "kda": f"{p.kills}/{p.deaths}/{p.assists}", "cs": int(p.creep_score)})
    names = _item_names(p.items)
    if names:
        d["objets"] = names
    if getattr(p, "is_dead", False):
        d["mort"] = True
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


def last_deaths(game: Any, limit: int = 3) -> list[dict[str, str]]:
    """My last deaths from the event feed: ``[{"temps": "12:04", "tue_par": "Darius"}]``."""
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
        killer = str(ev.get("KillerName") or "")
        out.append({"temps": _clock(ev.get("EventTime")),
                    "tue_par": lookup.get(killer.casefold()) or ("tourelle" if "turret" in killer.lower()
                                                                  else "sbire/monstre" if killer else "?")})
    return out[-limit:]


def build_snapshot(game: Any, *, moment: str = "", roles: Any = None, scoreboard: Any = None,
                   objectives: Iterable[Any] = (), item_text: str | None = None) -> dict[str, Any]:
    """Compact JSON-friendly game state for the prompt (no player names). Never raises."""
    snap: dict[str, Any] = {}
    try:
        gt = float(getattr(game, "game_time", 0.0) or 0.0)
        snap["temps_de_jeu"] = _clock(gt)
        if moment:
            snap["moment"] = MOMENT_FR.get(moment, moment)
        me = getattr(game, "me", None)
        if me is not None:
            mine = _player(me, "ally", None)
            try:
                r = roles.my_role() if roles is not None and hasattr(roles, "my_role") else None
            except Exception:
                r = None
            if r and ROLE_FR.get(str(r).upper()):
                mine["role"] = ROLE_FR[str(r).upper()]
            mine["or"] = int(getattr(game, "current_gold", 0.0) or 0)
            snap["moi"] = mine
        snap["allies"] = [_player(p, "ally", roles) for p in list(getattr(game, "allies", []))[:4]]
        snap["ennemis"] = [_player(p, "enemy", roles) for p in list(getattr(game, "enemies", []))[:5]]
        if scoreboard is not None and getattr(scoreboard, "players", None):
            snap["diff_or_equipe_estimee"] = int(scoreboard.team_gold_diff)
            snap["kills_equipes"] = f"{scoreboard.ally_kills}-{scoreboard.enemy_kills}"
        objs = []
        for o in objectives or ():
            name = getattr(o, "name", "")
            if not name:
                continue
            if getattr(o, "alive", False):
                objs.append({"objectif": name, "etat": "disponible"})
            elif getattr(o, "remaining", None) is not None and o.remaining < 600:
                objs.append({"objectif": name, "apparait_dans_s": int(o.remaining)})
        if objs:
            snap["objectifs"] = objs[:6]
        deaths = last_deaths(game)
        if deaths:
            snap["mes_dernieres_morts"] = deaths
        if item_text:
            snap["suggestion_objet_actuelle"] = str(item_text)[:160]
    except Exception:
        log.debug("AI snapshot incomplete", exc_info=True)
    return snap


def build_prompt(snapshot: dict[str, Any]) -> str:
    moment = snapshot.get("moment") or "point de situation"
    data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    return (f"Moment : {moment}.\nÉtat de la partie (JSON, API officielle) : {data}\n"
            "Réponds en 2 phrases courtes max, conseils concrets d'achat et de macro, "
            "pas de spéculation sur les temps de recharge ennemis.")


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
               threat: int = 0) -> bool:
        """Detect a key moment and maybe start a request. Returns True if one was started."""
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
                                  objectives=objectives, item_text=item_text)
            self._start(build_prompt(snap), moment, t)
            return True
        except Exception:
            log.exception("AIAdvisor.update failed")
            return False

    def poll(self) -> Advice | None:
        """The finished advice (once), else None."""
        with self._lock:
            res, self._result = self._result, None
            return res

    def _start(self, prompt: str, moment: str, t: float) -> None:
        prov, key, model = self._provider, self._key, self._model
        url = self._urls.get(prov)

        def job() -> None:
            try:
                text = self._caller(prov, key, model, SYSTEM_PROMPT, prompt, timeout=TIMEOUT_S, url=url)
                with self._lock:
                    self._result = Advice(text, moment, t)
                    self.calls += 1
            except AIError as exc:
                self._fail(exc.code, prov)
            except Exception:
                log.exception("AI request failed")
                self._fail("server", prov)

        th = threading.Thread(target=job, name="TreeAICoach-ai", daemon=True)
        self._thread = th
        th.start()

    def _fail(self, code: str, provider: str) -> None:
        log.info("AI advice unavailable (%s, %s)", provider, code)
        with self._lock:
            text = error_text(code, provider)
            if text != self._status:
                self._status_seq += 1
            self._status = text
            self._blocked_until = self._clock() + BACKOFF_S.get(code, 180.0)

    def wait(self, timeout: float = 10.0) -> bool:
        th = self._thread
        if th is not None:
            th.join(timeout)
        return not self.busy()


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
                      SYSTEM_PROMPT, prompt, timeout=TIMEOUT_S, url=url)
        return True, f"Connexion OK ({spec.label}) : {text}"
    except AIError as exc:
        return False, error_text(exc.code, prov)
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("AI test failed")
        return False, f"Conseil IA : erreur inattendue ({type(exc).__name__})."
