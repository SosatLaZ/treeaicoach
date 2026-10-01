"""ai_advisor.py: request / response formats against a local fake HTTP server, errors, policy."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from treeaicoach import ai_advisor as ai
from treeaicoach.live_client import GameInfo, PlayerInfo

RESPONSES = {
    "gemini": {"candidates": [{"content": {"parts": [{"text": "Achète **Zhonya**. Puis joue le dragon."}]}}]},
    "groq": {"choices": [{"message": {"role": "assistant", "content": "Achète Zhonya. Va mid."}}]},
    "openrouter": {"choices": [{"message": {"role": "assistant", "content": "Achète Zhonya. Va mid."}}]},
    "ollama": {"message": {"role": "assistant", "content": "Achète Zhonya. Va mid."}, "done": True},
    "anthropic": {"content": [{"type": "text", "text": "Achète Zhonya. Va mid."}], "role": "assistant"},
}


class _Server:
    def __init__(self, status: int = 200, payload: object = None) -> None:
        self.requests: list[dict] = []
        self.status = status
        self.payload = payload
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                outer.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                       "body": body})
                data = json.dumps(outer.payload).encode("utf-8")
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a: object) -> None:
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.th = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.th.start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = _Server()
    yield s
    s.close()


PATHS = {"gemini": "/v1beta/models/{model}:generateContent", "groq": "/openai/v1/chat/completions",
         "openrouter": "/api/v1/chat/completions", "ollama": "/api/chat", "anthropic": "/v1/messages"}


@pytest.mark.parametrize("provider", list(ai.PROVIDERS))
def test_each_provider_request_and_response(server, provider):
    server.payload = RESPONSES[provider]
    text = ai.call_llm(provider, "KEY123", "", "SYS", "PROMPT", url=server.url(PATHS[provider]))
    assert text.startswith("Achète Zhonya.") and "*" not in text
    req = server.requests[-1]
    h, b = req["headers"], req["body"]
    spec = ai.PROVIDERS[provider]
    if provider == "gemini":
        assert req["path"] == f"/v1beta/models/{spec.default_model}:generateContent"
        assert h["x-goog-api-key"] == "KEY123"
        assert b["contents"][0]["parts"][0]["text"] == "PROMPT"
        assert b["systemInstruction"]["parts"][0]["text"] == "SYS"
    elif provider in ("groq", "openrouter"):
        assert h["authorization"] == "Bearer KEY123"
        assert b["model"] == spec.default_model
        assert b["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "PROMPT"}]
    elif provider == "ollama":
        assert "authorization" not in h and b["stream"] is False and b["model"] == "llama3.1"
    else:
        assert h["x-api-key"] == "KEY123" and h["anthropic-version"] == "2023-06-01"
        assert b["system"] == "SYS" and b["model"] == "claude-haiku-4-5-20251001" and b["max_tokens"] > 0


def test_default_models():
    assert ai.PROVIDERS["gemini"].default_model == "gemini-2.0-flash"
    assert ai.PROVIDERS["groq"].default_model == "llama-3.3-70b-versatile"
    assert ai.PROVIDERS["openrouter"].default_model.endswith(":free")


def test_custom_model_used(server):
    server.payload = RESPONSES["groq"]
    ai.call_llm("groq", "k", "mixtral", "s", "p", url=server.url("/x"))
    assert server.requests[-1]["body"]["model"] == "mixtral"


@pytest.mark.parametrize("status,payload,code", [
    (401, {"error": "bad key"}, "key"),
    (400, {"error": {"message": "API key not valid. Please pass a valid API key."}}, "key"),
    (429, {"error": "rate"}, "quota"),
    (404, {"error": "no model"}, "model"),
    (500, {"error": "boom"}, "server"),
    (200, {"choices": []}, "empty"),
])
def test_http_errors(status, payload, code):
    s = _Server(status, payload)
    try:
        with pytest.raises(ai.AIError) as ei:
            ai.call_llm("groq", "k", "", "s", "p", url=s.url("/x"))
        assert ei.value.code == code
        assert ai.error_text(code)
    finally:
        s.close()


def test_offline_and_missing_key():
    with pytest.raises(ai.AIError) as ei:
        ai.call_llm("ollama", "", "", "s", "p", url="http://127.0.0.1:9/api/chat", timeout=1.0)
    assert ei.value.code == "offline"
    assert "Ollama" in ai.error_text("offline", "ollama")
    with pytest.raises(ai.AIError) as ei:
        ai.call_llm("gemini", "", "", "s", "p")
    assert ei.value.code == "nokey"


def test_clean_advice_two_sentences():
    out = ai.clean_advice("<think>hmm</think>**Un.** Deux ! Trois ? Quatre.")
    assert out == "Un. Deux !"
    assert len(ai.clean_advice("mot " * 200)) <= ai.MAX_ADVICE_CHARS + 1


def P(alias, team, name="", level=9, k=0, d=0, items=(), dead=False, pos=""):
    return PlayerInfo(riot_id=name or alias + "#EUW", summoner_name=name or alias, champion_alias=alias,
                      champion_name=alias, team=team, level=level, items=list(items), is_dead=dead, position=pos,
                      scores={"kills": k, "deaths": d, "assists": 1, "creepScore": 80, "wardScore": 3})


def G(gt=600.0, dead=False, level=9, gold=0.0, enemies=None, events=()):
    me = P("Ahri", "ORDER", name="Moi#EUW", level=level, dead=dead, items=[3020], pos="MIDDLE")
    return GameInfo(game_time=gt, me=me, allies=[P("Garen", "ORDER", pos="TOP")],
                    enemies=enemies or [P("Zed", "CHAOS", pos="MIDDLE")], events=list(events), current_gold=gold,
                    map_number=11)


def test_snapshot_and_prompt_no_names():
    ev = [{"EventName": "ChampionKill", "EventTime": 500.0, "VictimName": "Moi", "KillerName": "Zed"}]
    snap = ai.build_snapshot(G(events=ev, gold=1500), moment="death", item_text="Prochain objet : Zhonya")
    assert snap["moi"]["champion"] == "Ahri" and snap["moi"]["or"] == 1500 and snap["moi"]["role"] == "mid"
    assert snap["ennemis"][0]["champion"] == "Zed"
    assert snap["mes_dernieres_morts"] == [{"temps": "8:20", "tue_par": "Zed"}]
    prompt = ai.build_prompt(snap)
    assert "2 phrases courtes max" in prompt and "Moi#EUW" not in prompt
    json.loads(prompt.split("API officielle) : ", 1)[1].split("\n", 1)[0])


def test_moment_detector():
    d = ai.MomentDetector()
    assert d.update(G()) is None
    assert d.update(G(dead=True)) == "death"
    assert d.update(G(level=11)) == "level"
    assert d.update(G(gold=1200), in_base=True) == "base"
    assert d.update(G(gold=1200), in_base=True) is None
    fed = P("Zed", "CHAOS", k=6, d=1)
    assert d.update(G(enemies=[fed])) == "fed"
    obj = SimpleNamespace(key="baron", name="Baron", alive=False, remaining=60.0, next_spawn=1200.0)
    assert d.update(G(enemies=[fed]), objectives=[obj]) == "objective"
    assert d.update(G(enemies=[fed]), objectives=[obj]) is None


def test_advisor_policy_rate_limit_and_errors():
    calls = []

    def caller(prov, key, model, system, prompt, timeout, url):
        calls.append((prov, prompt))
        if len(calls) == 2:
            raise ai.AIError("quota")
        return "Achète Zhonya."

    cfg = SimpleNamespace(ai_provider="groq", ai_api_key="k", ai_model="")
    now = [0.0]
    adv = ai.AIAdvisor(cfg, caller=caller, clock=lambda: now[0])
    adv.update(0.0, G())
    assert adv.update(1.0, G(dead=True)) and adv.wait()
    res = adv.poll()
    assert res is not None and res.text == "Achète Zhonya." and res.moment == "death" and adv.poll() is None
    assert not adv.update(30.0, G(level=11))                  # rate limited (90 s)
    assert adv.update(200.0, G(level=16)) and adv.wait()      # quota error
    seq, status = adv.status()
    assert seq == 1 and "quota" in status and adv.poll() is None
    now[0] = 300.0
    assert not adv.update(400.0, G(dead=True))                # back-off
    off = ai.AIAdvisor(SimpleNamespace(ai_provider="off"), caller=caller)
    off.update(0, G())
    assert not off.update(500.0, G(dead=True))


def test_advisor_never_raises():
    adv = ai.AIAdvisor(SimpleNamespace(ai_provider="gemini", ai_api_key="k"))
    assert adv.update(0.0, object()) is False
    assert adv.update(0.0, None) is False


def test_check_connection(server):
    server.payload = RESPONSES["anthropic"]
    ok, msg = ai.check_connection(SimpleNamespace(ai_provider="anthropic", ai_api_key="k", ai_model=""),
                                  url=server.url("/v1/messages"))
    assert ok and "Connexion OK" in msg
    ok, msg = ai.check_connection(SimpleNamespace(ai_provider="off"))
    assert not ok
    ok, msg = ai.check_connection(SimpleNamespace(ai_provider="groq", ai_api_key="", ai_model=""))
    assert not ok and "clé" in msg
