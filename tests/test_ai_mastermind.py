"""ai_advisor.py v3 ("mastermind"): regression tests of the AI bugs found in the live Groq audit
(2026-10) + the compact game snapshot, the card consistency, the engine publishing rules and the
JSON post-game review. No network: local fake servers / fake callers only."""
from __future__ import annotations

import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from test_ai_advisor import PATHS, G, P

from treeaicoach import ai_advisor as ai

CFG = NS(ai_provider="groq", ai_api_key="k", ai_model="")
PLAN = {"plan": "Rentre maintenant : dragon dans 1:20.", "etapes": ["Pose une balise au pixel du bas."],
        "objectif": "dragon", "urgence": "haute", "carte": "suit", "pourquoi": ""}


class SeqServer:
    """A local HTTP server answering a SEQUENCE of (status, payload, headers) (the last repeats)."""

    def __init__(self, seq: list[tuple[int, object, dict]], get_payload: object = None) -> None:
        self.seq = list(seq)
        self.requests: list[dict] = []
        self.get_payload = get_payload
        outer = self

        class H(BaseHTTPRequestHandler):
            def _send(self, status: int, payload: object, headers: dict) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                outer.requests.append({"method": "POST", "path": self.path,
                                       "body": json.loads(self.rfile.read(n).decode("utf-8"))})
                i = min(len(outer.requests_post()) - 1, len(outer.seq) - 1)
                self._send(*outer.seq[i])

            def do_GET(self) -> None:  # noqa: N802
                outer.requests.append({"method": "GET", "path": self.path})
                self._send(200 if outer.get_payload is not None else 404, outer.get_payload, {})

            def log_message(self, *a: object) -> None:
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def requests_post(self) -> list[dict]:
        return [r for r in self.requests if r["method"] == "POST"]

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}{path}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _ok(content: str) -> tuple[int, object, dict]:
    return 200, {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}, {}


# ------------------------------------------------------------------------------ provider layer
def test_bug_429_per_minute_is_transient_not_a_quota():
    """Bug: every 429 was "quota" (10 min back-off, then self-check rule 8 stopped the AI for the
    whole game). Groq's free tier is 8 000 tokens / minute (~4 plans): a TPM 429 is a short wait."""
    tpm = '{"error":{"message":"Rate limit reached for model `openai/gpt-oss-120b` on tokens per minute (TPM): ' \
          'Limit 8000, Used 7900, Requested 1900. Please try again in 7.5s.","code":"rate_limit_exceeded"}}'
    assert ai._classify_http(429, tpm) == ("rate", 7.5)
    assert ai._classify_http(429, "requests per day (RPD): Limit 1000. Please try again in 1m26.4s.") == ("quota", 86.4)
    assert ai._classify_http(429, '{"error":{"status":"RESOURCE_EXHAUSTED","details":[{"retryDelay":"34s"}]}}') \
        == ("rate", 34.0)
    assert ai._classify_http(429, "x", {"Retry-After": "2"}) == ("rate", 2.0)
    # the real Groq TPM body ends with a billing link: still a per-minute limit (live bug, 2026-10)
    groq = ('{"error":{"message":"Rate limit reached for model `openai/gpt-oss-120b` in organization `org_x` '
            'service tier `on_demand` on tokens per minute (TPM): Limit 8000, Used 6100, Requested 3900. Please '
            'try again in 15.0s. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/'
            'billing","type":"tokens","code":"rate_limit_exceeded"}}')
    assert ai._classify_http(429, groq) == ("rate", 15.0)
    gem = ("Quota exceeded for quota metric 'Generate Content API requests per minute' and limit "
           "'GenerateContent request limit per minute for a region' ... retryDelay: 20s")
    assert ai._classify_http(429, gem)[0] == "rate"
    assert ai._classify_http(529, '{"type":"error","error":{"type":"overloaded_error"}}')[0] == "server"
    assert ai._classify_http(404, "models/gemini-2.0-flash is no longer available to new users")[0] == "model"


def test_bug_429_short_wait_is_retried_once_then_succeeds():
    s = SeqServer([(429, {"error": {"message": "Please try again in 0.2s", "code": "rate_limit_exceeded"}}, {}),
                   _ok("Va mid.")])
    try:
        t0 = time.monotonic()
        assert ai.call_llm("groq", "k", "", "s", "p", url=s.url(PATHS["groq"])) == "Va mid."
        assert len(s.requests_post()) == 2 and time.monotonic() - t0 < 3.0
    finally:
        s.close()
    s = SeqServer([(429, {"error": {"message": "Please try again in 20s"}}, {})])
    try:
        with pytest.raises(ai.AIError) as ei:                 # a long wait: not retried in the thread
            ai.call_llm("groq", "k", "", "s", "p", url=s.url(PATHS["groq"]))
        assert ei.value.code == "rate" and ei.value.retry_after == 20.0 and len(s.requests_post()) == 1
    finally:
        s.close()


def test_bug_rate_backoff_uses_the_provider_wait_and_selfcheck_does_not_block():
    from test_selfcheck import drive

    from treeaicoach.selfcheck import SelfCheck, Snapshot

    now = {"t": 100.0}

    def caller(*a, **k):
        raise ai.AIError("rate", "HTTP 429", retry_after=7.5)

    adv = ai.AIAdvisor(CFG, caller=caller, clock=lambda: now["t"])
    assert adv.ask(100.0, G(600.0)) == "Question envoyée à l'IA…"
    adv.wait()
    assert adv._blocked_until == pytest.approx(100.0 + ai.RATE_BACKOFF_MIN_S)   # max(15, 7.5 + 2)
    assert adv.poll().text == ai.ERROR_FR["rate"]
    # rule 8: a per-minute limit is never a reason to stop the AI for the game
    from treeaicoach import selfcheck as SC

    sc = SelfCheck()
    st = ai.ERROR_FR["rate"]
    acts = drive(sc, 0, 40, lambda t: Snapshot(t=t, ai_enabled=True, ai_seq=1, ai_status=st,
                                               ai_code=SC._ai_code(st), ai_backoff_until=100.0 + 20 * (t // 5)))
    assert acts == []


def test_bug_key_test_reports_a_working_key_on_rate_limit():
    from treeaicoach import ui_kit

    def caller(*a, **k):
        raise ai.AIError("rate", "HTTP 429", retry_after=5.0)

    ok, short, msg = ui_kit.test_ai_key(CFG, caller=caller)
    assert ok and short == "clé OK" and "limite par minute" in msg
    ok, msg = ai.check_connection(CFG, caller=caller)
    assert ok and "clé valide" in msg


def test_bug_model_substitution_is_remembered_for_a_configured_model():
    """Bug: with a configured model that no longer exists (llama-3.3-70b-versatile on Groq, 2026),
    EVERY request did 404 + model list + retry (3 HTTP calls, ~1 s more)."""
    s = SeqServer([(400, {"error": {"message": "The model `llama-3.3-70b-versatile` has been decommissioned",
                                     "code": "model_decommissioned"}}, {}), _ok("Va mid.")],
                  get_payload={"data": [{"id": "canopylabs/orpheus-v1-english"}, {"id": "openai/gpt-oss-safeguard-20b"},
                                        {"id": "openai/gpt-oss-120b"}]})
    ai._auto_model.clear()
    try:
        url = s.url(PATHS["groq"])
        assert ai.call_llm("groq", "k", "llama-3.3-70b-versatile", "s", "p", url=url) == "Va mid."
        assert ai.call_llm("groq", "k", "llama-3.3-70b-versatile", "s", "p", url=url) == "Va mid."
        models = [r["body"]["model"] for r in s.requests_post()]
        assert models == ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-120b"]
        assert len([r for r in s.requests if r["method"] == "GET"]) == 1
    finally:
        ai._auto_model.clear()
        s.close()


def test_bug_pick_model_never_picks_speech_or_safety_models():
    live_2026 = ["allam-2-7b", "canopylabs/orpheus-arabic-saudi", "canopylabs/orpheus-v1-english",
                 "meta-llama/llama-prompt-guard-2-22m", "openai/gpt-oss-safeguard-20b", "whisper-large-v3",
                 "qwen/qwen3.8-27b"]
    assert ai.pick_model("groq", live_2026) == "qwen/qwen3.8-27b"
    assert ai.pick_model("groq", ["canopylabs/orpheus-v1-english", "openai/gpt-oss-safeguard-20b"]) is None
    assert ai.pick_model("gemini", ["gemini-2.0-flash", "gemini-3.5-flash-lite", "imagen-4"]) == "gemini-3.5-flash-lite"


def test_bug_openrouter_error_in_a_200_body():
    """OpenRouter forwards an upstream 429 / 5xx with HTTP 200 and ``{"error": {...}}``: it was an
    "empty answer" (90 s back-off, wrong message)."""
    s = SeqServer([(200, {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-min. "
                                                              "Please try again in 30s"}}, {})])
    try:
        with pytest.raises(ai.AIError) as ei:
            ai.call_llm("openrouter", "k", "", "s", "p", url=s.url(PATHS["openrouter"]))
        assert ei.value.code == "rate" and ei.value.retry_after == 30.0
    finally:
        s.close()


def test_gemini_thinking_budget_thought_parts_and_blocked_prompt():
    _u, _h, body = ai.build_request("gemini", "gemini-2.5-flash", "k", "s", "p", max_tokens=320, json_mode=True)
    gen = json.loads(body)["generationConfig"]
    assert gen["thinkingConfig"] == {"thinkingBudget": 0} and gen["maxOutputTokens"] >= ai.GEMINI_MIN_TOKENS
    gen = json.loads(ai.build_request("gemini", "", "k", "s", "p")[2])["generationConfig"]
    assert gen["thinkingConfig"] == {"thinkingLevel": "low"}                   # default: gemini 3.x
    gen = json.loads(ai.build_request("gemini", "gemini-2.0-flash", "k", "s", "p")[2])["generationConfig"]
    assert "thinkingConfig" not in gen
    data = {"candidates": [{"content": {"parts": [{"text": "réflexion", "thought": True}, {"text": "Va mid."}]}}]}
    assert ai.parse_response("gemini", data) == "Va mid."
    with pytest.raises(ai.AIError) as ei:
        ai.parse_response("gemini", {"promptFeedback": {"blockReason": "SAFETY"}})
    assert ei.value.code == "empty"


def test_ollama_think_only_for_thinking_models_and_anthropic_refusal():
    b = json.loads(ai.build_request("ollama", "qwen3:8b", "", "s", "p", json_mode=True)[2])
    assert b["think"] is False and b["format"] == "json" and b["options"]["num_predict"] >= ai.GROQ_MIN_TOKENS
    assert json.loads(ai.build_request("ollama", "gpt-oss:20b", "", "s", "p")[2])["think"] == "low"
    assert "think" not in json.loads(ai.build_request("ollama", "llama3.1", "", "s", "p")[2])
    b = json.loads(ai.build_request("openrouter", "openai/gpt-oss-120b:free", "k", "s", "p")[2])
    assert b["reasoning"] == {"effort": "low", "exclude": True}
    with pytest.raises(ai.AIError):
        ai.parse_response("anthropic", {"content": [], "stop_reason": "refusal"})


def test_bug_truncated_json_plan_is_salvaged():
    """``finish_reason: length``: the plan was thrown away (counted as broken JSON)."""
    cut = '{"plan": "Rentre maintenant : dragon dans 1:20.", "etapes": ["Pose une balise au pixel.", "Pousse la va'
    p = ai.parse_plan(cut)
    assert p["plan"] == "Rentre maintenant : dragon dans 1:20." and p["etapes"] == ["Pose une balise au pixel."]
    assert ai.parse_plan('{"plan": "Va mid." "etapes": []}') is None              # complete but broken: rejected
    with pytest.raises(ai.AIError):
        ai.parse_response("groq", {"choices": [{"message": {"content": "<think>raisonnement coupé"}}]}, raw=True)


# ------------------------------------------------------------------------------ advisor policy
def _plan_caller(calls, gate=None, payload=PLAN):
    def caller(prov, key, model, system, prompt, **kw):
        calls.append(prompt)
        if gate is not None:
            gate.wait(5.0)
        return json.dumps(payload, ensure_ascii=False)
    return caller


def test_bug_no_double_request_between_engine_and_hotkey_threads():
    """Bug: update() (engine thread) and ask() (F8 / UI thread) both checked "no thread alive"
    then started their own request: two requests in flight, one answer lost."""
    calls, gate = [], threading.Event()
    adv = ai.AIAdvisor(CFG, caller=_plan_caller(calls, gate))
    assert adv.ask(0.0, G(600.0)) == "Question envoyée à l'IA…"
    assert adv.busy()
    assert adv.ask(30.0, G(600.0)) == "L'IA réfléchit déjà…"
    adv.detector.update(G(600.0, dead=False))
    assert not adv.update(200.0, G(610.0, dead=True))          # a key moment while a request is in flight
    gate.set()
    adv.wait()
    assert len(calls) == 1 and adv.poll().moment == "manual"


def test_bug_answer_of_the_previous_game_is_never_shown():
    calls, gate = [], threading.Event()
    adv = ai.AIAdvisor(CFG, caller=_plan_caller(calls, gate))
    adv.ask(0.0, G(600.0))
    adv.reset()                                                 # a new game starts meanwhile
    gate.set()
    adv.wait()
    assert adv.poll() is None and not adv.busy()


def test_bug_hard_cap_counts_failed_requests():
    """Bug: a failed request refunded its slot AND left the hard cap untouched: an AI that keeps
    failing could be called far more than GAME_HARD_CAP times in a game."""
    def caller(*a, **k):
        raise ai.AIError("server", "HTTP 500")

    adv = ai.AIAdvisor(CFG, caller=caller, clock=lambda: 0.0)
    for i in range(ai.GAME_HARD_CAP):
        adv._blocked_until = -math.inf
        adv._last_call = -math.inf
        assert adv.ask(1000.0 * (i + 1), G(600.0)) == "Question envoyée à l'IA…"
        adv.wait()
        adv.poll()
    assert adv.budget.sent == ai.GAME_HARD_CAP and adv.budget.exhausted
    adv._blocked_until = -math.inf
    assert "Limite de 10" in adv.ask(99999.0, G(600.0))


def test_bug_unusable_answer_gives_the_slot_back():
    calls = []
    adv = ai.AIAdvisor(CFG, caller=lambda *a, **k: calls.append(1) or '{"plan": 3}', clock=lambda: 0.0)
    adv.detector.update(G(600.0))
    assert adv.update(100.0, G(610.0, dead=True)) and adv.wait()
    assert adv.budget.auto_used == 0 and adv.budget.sent == 1 and adv.json_errors == 1
    res = adv.poll()
    assert res is None or res.source == "rules"


def test_bug_key_moment_during_a_fight_is_deferred_not_lost():
    calls = []
    adv = ai.AIAdvisor(CFG, caller=_plan_caller(calls), clock=lambda: 0.0)
    adv.detector.update(G(600.0))
    assert not adv.update(100.0, G(610.0, dead=True), in_fight=True)         # death moment in a fight
    assert not adv.update(105.0, G(615.0, dead=True), threat=1)               # gank threat right after
    assert adv.update(110.0, G(620.0, dead=True)) and adv.wait()              # calm: the moment now
    assert adv.poll().moment == "death" and len(calls) == 1
    adv2 = ai.AIAdvisor(CFG, caller=_plan_caller([]), clock=lambda: 0.0)
    adv2.detector.update(G(600.0))
    adv2.update(100.0, G(610.0, dead=True), in_fight=True)
    assert not adv2.update(100.0 + ai.DEFER_S + 1, G(650.0, dead=True))       # too late: dropped


def test_window_moments_use_the_objective_slot_before_mid_game():
    b = ai.AIBudget()
    assert b.pick("comeback:carries_dead", 10 * 60.0) == "objective"
    assert b.pick("comeback:ace", 30 * 60.0) == "objective"


# ------------------------------------------------------------------------------ card consistency
def test_card_check_stances_and_objectives():
    retreat = NS(text="Recule vers ta tour : Darius est niveau 6, pas toi", why="Il a son ultime.")
    assert ai.card_check("Prends le dragon maintenant.", retreat) == "conflict"
    assert ai.card_check("Recule sous ta tour, puis farme la vague.", retreat) == "ok"
    assert ai.card_check("Achète Gage de Sterak.", retreat) == "ok"
    assert ai.card_check("Frappe Darius.", retreat, stance="differe", why="Darius a 10 % de vie.") == "explained"
    baron = "Prends le Baron maintenant : 3 ennemis morts"
    assert ai.card_check("Prends le dragon maintenant.", baron) == "conflict"
    assert ai.card_check("Prends le Baron puis pousse le mid.", baron) == "ok"
    assert ai.card_check("Prends le dragon.", None) == "ok"
    adv = ai.Advice("Prends le dragon.", "death", 0.0, source="rules", card="suit")
    assert adv.check_card(retreat) == "ok"                       # the offline plan copies the card


def _publisher(card=None, fight=False, toasts=True, skill="intermediaire"):
    from treeaicoach.engine_coaching import CoachingMixin
    from treeaicoach.presenter import Presenter

    shown = []

    class Fake(CoachingMixin):
        def __init__(self) -> None:
            self._cfg = NS(ai_speak=False, toasts_enabled=True, skill_level=skill)
            self._tactics = NS(macro_active=lambda: card, in_fight=lambda: fight)
            self._toasts = NS(push=lambda *a, **k: shown.append(a)) if toasts else None
            self._presenter = Presenter()
            self.text_messages = []
            self._text_msg = None

        def _topic_seen(self, key, t):
            return False

        def _presenter_ctx(self, t):
            from treeaicoach.presenter import Context
            return Context(t=float(t), fight=bool(self._tactics.in_fight()), skill=skill)

        def _keeps_death_lesson(self, mk):
            return False

    return Fake(), shown


class _Queue:
    def __init__(self, *advs):
        self.advs = list(advs)
        self.dropped, self.shown = [], []

    def poll(self):
        return self.advs.pop(0) if self.advs else None

    def note_dropped(self, adv, why):
        self.dropped.append(why)

    def note_shown(self, adv, gt=None):
        self.shown.append(adv.text)


def test_bug_stale_plan_is_dropped_and_never_shown_late():
    eng, _shown = _publisher()
    q = _Queue(ai.Advice("Rentre maintenant : dragon dans 1:20.", "objective", 100.0))
    eng._ai_publish(100.0 + ai.PLAN_MAX_AGE_S + 1, 700.0, q, 0, False)
    assert q.dropped == ["stale"] and eng._text_msg is None


def test_bug_plan_waits_for_the_end_of_the_fight():
    """Bug: the engine wrote the AI line straight into the HUD (``_text_msg``), bypassing the
    presenter: an AI plan appeared in the middle of a fight."""
    eng, _shown = _publisher(fight=True)
    q = _Queue(ai.Advice("Rentre maintenant : dragon dans 1:20.", "objective", 100.0))
    eng._ai_publish(102.0, 700.0, q, 0, True)
    assert eng._text_msg is None and eng._ai_held is not None
    eng._tactics = NS(macro_active=lambda: None, in_fight=lambda: False)
    eng._ai_publish(110.0, 708.0, q, 0, False)
    assert q.shown == ["Rentre maintenant : dragon dans 1:20."]


def test_bug_plan_contradicting_the_card_is_never_shown():
    card = NS(text="Recule vers ta tour : Darius est niveau 6, pas toi", why="x")
    eng, _shown = _publisher(card=card)
    q = _Queue(ai.Advice("Prends le dragon maintenant.", "objective", 100.0))
    eng._ai_publish(101.0, 700.0, q, 0, False)
    assert q.dropped == ["conflict"] and not q.shown
    # an explained disagreement waits for the card to go, then shows with its one-line reason
    q = _Queue(ai.Advice("Prends le dragon. Pourquoi pas la carte : 3 ennemis morts.", "objective", 200.0,
                         card="differe", why="3 ennemis morts"))
    eng._ai_publish(201.0, 800.0, q, 0, False)
    assert not q.shown and eng._ai_held is not None
    eng._tactics = NS(macro_active=lambda: None, in_fight=lambda: False)
    eng._ai_publish(205.0, 804.0, q, 0, False)
    assert q.shown and "Pourquoi pas la carte" in q.shown[0]


def test_bug_f8_answer_reaches_an_expert_player():
    """The AI line now goes through the presenter (it used to be written straight into the HUD,
    fights included). The presenter's value bar for experts (0.75) is above the "ai" route (0.5)
    and a busy panel holds ordinary lines: the answer to F8 (urgency 2) must still get through."""
    for toasts in (True, False):
        eng, _shown = _publisher(toasts=toasts, skill="expert")
        from treeaicoach import presenter as prs

        ack = eng._presenter.offer(prs.Message("ai", "Question envoyée à l'IA…", topic="ai-ask:100"),
                                   eng._presenter_ctx(100.0))
        assert ack.channel == prs.DROP                       # (expert: the ack itself is below the bar)
        eng._presenter.offer(prs.Message("insight", "Farme la vague.", urgency=1, value=0.9, topic="tip"),
                             eng._presenter_ctx(100.0))     # the panel is busy with an ordinary line
        q = _Queue(ai.Advice("Rentre et achète Gage de Sterak.", "manual", 100.0))
        eng._ai_publish(101.5, 700.0, q, 0, False)
        assert q.shown == ["Rentre et achète Gage de Sterak."], toasts
        assert eng._text_msg == (101.5, "Rentre et achète Gage de Sterak.")
        q = _Queue(ai.Advice("Pousse la vague.", "base", 102.0))          # an ordinary auto plan: not for experts
        eng._ai_publish(103.0, 702.0, q, 0, False)
        assert not q.shown and eng._ai_held is not None                    # retried while fresh...
        eng._ai_publish(102.0 + ai.PLAN_MAX_AGE_S - 0.5, 720.0, q, 0, False)
        assert not q.shown and q.dropped == ["presenter"]                  # ... then dropped, never shown


def test_bug_ai_knows_about_fights():
    from treeaicoach.engine_coaching import CoachingMixin

    fake = NS(_tactics=NS(in_fight=lambda: True))
    assert CoachingMixin._ai_in_fight(fake) is True                 # was always False (missing attributes)


# ------------------------------------------------------------------------------ mastermind snapshot
def _full_context():
    return {"ph": "milieu", "carte": {"appel": "Prends le dragon maintenant : Lee Sin est mort",
                                      "pourquoi": "Leur jungler réapparaît dans 40 s."},
            "diff": {"eq": -1200, "kills": "6-9", "po": -400, "niv": -1, "cs": -15, "vs": "Darius", "vs_alias": "Darius"},
            "conseils": ["Recule quand il a 5 saignements : son R exécute", "Prends les échanges courts au niveau 3"],
            "compo": {"nous": ai.team_profile(["Garen", "LeeSin", "Ahri", "Jinx", "Thresh"]),
                      "eux": ai.team_profile(["Darius", "Vi", "Zed", "Caitlyn", "Lux"]),
                      "plan": "Gagne les combats d'équipe après 25 min : vous scalez mieux."},
            "effets": {"anti-soin": ["Darius"], "armure": ["Malphite x2"], "stase": ["Zed"]},
            "jgl": {"c": "Vi", "lv": 11, "vu": "rivière du bas, il y a 12 s", "farm": "bot",
                    "txt": "Vi farme côté bas"},
            "carto": {"dragons": "1-2", "tours_perdues": "nous 2, eux 1"},
            "causes": [[600, "Recule plus tôt : mort à 1 contre 2"]],
            "coups": ["11:10 blunder : Mort dans le brouillard.", "12:30 great : Plaque prise."], "prec": 64,
            "prio": {"bot": "prio (vague chez eux)", "mid": "équilibrée", "top": "vague chez nous"},
            "balises": ["Pixel du bas", "Rivière du bas"], "wp": 41,
            "plus": {"ressources": "mana 30 %, R prêt"}}


def test_mastermind_snapshot_is_complete_compact_and_deterministic():
    from treeaicoach.live_client import parse_allgamedata

    g = parse_allgamedata(json.loads((Path(__file__).parent / "fixtures" / "allgamedata_sample.json")
                                     .read_text(encoding="utf-8")))
    objs = [NS(key="dragon", name="Dragon", alive=False, remaining=75.0), NS(key="baron", name="Baron", alive=True,
                                                                              remaining=0.0)]
    snap = ai.build_snapshot(g, moment="objective", context=_full_context(), objectives=objs,
                             item_text="Prochain objet : Gage de Sterak.", trend="-800 en 2 min")
    for k in ("t", "ph", "mo", "carte", "me", "voie", "eq", "al", "en", "compo", "effets", "jgl", "obj", "carto",
              "coups", "prec", "achat", "objets_possibles", "wp"):
        assert k in snap, k
    order = [k for k in ai.SNAP_ORDER if k in snap]
    assert [k for k in snap if k in ai.SNAP_ORDER] == order
    assert snap["obj"] == [["Baron", 0], ["Dragon", 75]] and snap["eq"]["tend"] == "-800 en 2 min"
    assert snap["voie"]["conseils"] and snap["compo"]["eux"].startswith("dégâts")
    assert ai.snapshot_tokens(snap) < 1500
    prompt = ai.build_prompt(snap)
    assert ai.build_prompt(ai.build_snapshot(g, moment="objective", context=_full_context(), objectives=objs,
                                             item_text="Prochain objet : Gage de Sterak.",
                                             trend="-800 en 2 min")) == prompt
    assert "Prends le dragon maintenant" in prompt and "carte" in ai.PLAN_SCHEMA_FR


def test_team_profile_and_effects_from_enemy_items():
    prof = ai.team_profile(["Malphite", "Amumu", "Orianna", "Jinx", "Leona"])
    assert prof.startswith("dégâts") and "engage" in prof and "contrôle" in prof
    darius = P("Darius", "CHAOS", items=[3076, 3075])          # Pare-balles / Bramble + Thornmail if known
    g = G(enemies=[darius])
    eff = ai._ctx_effects(None, g, 0.0)
    assert eff is None or all(isinstance(v, list) for v in eff.values())


def test_timeline_and_trend_are_recorded():
    adv = ai.AIAdvisor(CFG, caller=_plan_caller([]), clock=lambda: 0.0)
    for i in range(30):
        gt = 600.0 + 10.0 * i
        sb = NS(players=(1,), team_gold_diff=-50 * i, ally_kills=3, enemy_kills=4 + i // 10)
        adv.update(float(i), G(gt), scoreboard=sb)
    assert adv.trend(890.0).endswith("en 2 min") and adv.trend(890.0).startswith("-")
    assert adv.timeline and adv.timeline[0].startswith("10:00 or +0")


# ------------------------------------------------------------------------------ post-game review
ANALYSIS = {"summary": {"champion": "Garen", "champion_name": "Garen", "position": "TOP", "kills": 1, "deaths": 9,
                        "assists": 5, "cs_per_min": 5.2, "vision_per_min": 0.4, "items": [3047, 6631, 3071, 1055],
                        "enemies": [{"alias": "Vladimir", "name": "Vladimir"}, {"alias": "Kindred", "name": "Kindred"}]},
            "death_verdicts": {"ignored": 3, "duel": 2, "late": 1}, "alert_lead": {"n": 4, "mean": 4.5}}


def test_bug_review_keeps_3_axes_control_wards_and_french_verdicts():
    """Live Groq review (2026-10): axis 3 ("place une balise de contrôle...") was dropped by the
    item grounding (a consumable is not a build item) -> 2 axes only; the prompt carried the
    verdict keys in English ("2 ignored, 1 duel"); the replacement items line said "vise <an item
    already in the build>"."""
    seen = {}
    answer = {"forces": ["Tu as participé à 6 kills sur 10.", "Bon contrôle des vagues en début de partie."],
              "axes": [{"axe": "Recule dès l'alerte", "preuve": "3 morts sur alerte ignorée",
                        "exercice": "À chaque alerte, fais 3 pas vers ta tour."},
                       {"axe": "Pose plus de balises", "preuve": "vision 0.4/min",
                        "exercice": "Achète une balise de contrôle à chaque retour et place-la dans la rivière."},
                       {"axe": "Construis Titanic Hydra en premier", "preuve": "dégâts faibles",
                        "exercice": "Rush Titanic Hydra."}],
              "objets": "Achète Hydre titanesque."}

    def caller(prov, key, model, system, prompt, **kw):
        seen.update(system=system, prompt=prompt, kw=kw)
        return json.dumps(answer, ensure_ascii=False)

    review = ai.postgame_review(CFG, ANALYSIS, caller=caller, timeline=["10:00 or -300 kills 3-4", "12:00 plan IA : Rentre."])
    assert seen["kw"]["json_mode"] is True and "EXACTEMENT 3 axes" in seen["system"]
    assert "3 alerte ignorée" in seen["prompt"] and "ignored" not in seen["prompt"].split("Chiffres à citer", 1)[1]
    assert "Déroulé" in seen["prompt"] and "12:00 plan IA" in seen["prompt"]
    lines = review.splitlines()
    assert lines[0].startswith("Points forts")
    axes = [x for x in lines if x[:2] in ("1.", "2.", "3.")]
    assert len(axes) == 3 and "balise de contrôle" in review
    for bad in ("Titanic", "Hydra", "Hydre titanesque"):
        assert bad not in review, bad
    obj = next(x for x in lines if x.startswith("Objets :"))
    build = {"Coques en acier", "Estropieur", "Couperet noir", "Épée longue"}
    assert "vise" in obj.lower() and not any(f"vise {b}" in obj for b in build)


def test_review_rule_axes_fill_a_short_answer():
    answer = {"forces": ["Bon début."], "axes": [{"axe": "Farme mieux", "preuve": "5.2 CS/min", "exercice": "80 sbires."}],
              "objets": ""}
    review = ai.postgame_review(CFG, ANALYSIS, caller=lambda *a, **k: json.dumps(answer, ensure_ascii=False))
    assert len([x for x in review.splitlines() if x[:2] in ("1.", "2.", "3.")]) == 3


def test_bug_review_waits_out_a_per_minute_limit_once():
    """Live: the review right after the game's last plans hit Groq's TPM limit and was lost."""
    calls = []

    def caller(prov, key, model, system, prompt, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise ai.AIError("rate", "HTTP 429", retry_after=0.1)
        return json.dumps({"forces": ["Bien."], "axes": [{"axe": "Farme mieux", "preuve": "5.2 CS/min",
                                                          "exercice": "80 sbires."}], "objets": ""})

    assert ai.postgame_review(CFG, ANALYSIS, caller=caller) and len(calls) == 2


def test_bug_review_keeps_decimal_numbers_and_french_words():
    """Live: "vision 0.44/min" came out as "vision 0. 44/min" (sentence split on the decimal point)."""
    answer = {"forces": ["Participation de 80 %."], "axes": [
        {"axe": "Améliore ta vision en lane", "preuve": "vision 0.44/min", "exercice": "Pose 2 balises par retour."},
        {"axe": "Farme mieux", "preuve": "5.96 CS/min", "exercice": "80 sbires en 10 min."},
        {"axe": "Recule dès l'alerte", "preuve": "2 alertes ignorées", "exercice": "3 pas vers ta tour."}],
        "objets": ""}
    review = ai.postgame_review(CFG, ANALYSIS, caller=lambda *a, **k: json.dumps(answer, ensure_ascii=False))
    assert "0.44/min" in review and "5.96 CS/min" in review and " lane" not in review
