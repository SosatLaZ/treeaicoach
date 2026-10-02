"""ai_advisor.py: request / response formats against a local fake HTTP server, errors, policy."""
from __future__ import annotations

import json
import threading
from pathlib import Path
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
        self.get_payload: object = None
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

            def do_GET(self) -> None:  # noqa: N802
                outer.requests.append({"path": self.path, "method": "GET",
                                       "headers": {k.lower(): v for k, v in self.headers.items()}})
                data = json.dumps(outer.get_payload).encode("utf-8")
                self.send_response(200 if outer.get_payload is not None else 404)
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
        if provider == "groq":
            assert b["reasoning_effort"] == "low" and b["include_reasoning"] is False and b["max_tokens"] >= 500
        assert b["model"] == spec.default_model
        assert b["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "PROMPT"}]
    elif provider == "ollama":
        assert "authorization" not in h and b["stream"] is False and b["model"] == "llama3.1"
    else:
        assert h["x-api-key"] == "KEY123" and h["anthropic-version"] == "2023-06-01"
        assert b["system"] == "SYS" and b["model"] == "claude-haiku-4-5" and b["max_tokens"] > 0


def test_default_models():
    assert ai.PROVIDERS["gemini"].default_model == "gemini-3.5-flash-lite"     # 2.0 / 2.5: legacy accounts only
    assert ai.PROVIDERS["groq"].default_model == "openai/gpt-oss-120b"
    assert ai.PROVIDERS["openrouter"].default_model.endswith(":free")


def test_custom_model_used(server):
    server.payload = RESPONSES["groq"]
    ai.call_llm("groq", "k", "mixtral", "s", "p", url=server.url("/x"))
    assert server.requests[-1]["body"]["model"] == "mixtral"


@pytest.mark.parametrize("status,payload,code", [
    (401, {"error": "bad key"}, "key"),
    (400, {"error": {"message": "API key not valid. Please pass a valid API key."}}, "key"),
    (429, {"error": "rate"}, "rate"),
    (429, {"error": {"message": "Rate limit reached on requests per day (RPD): Limit 1000"}}, "quota"),
    (429, {"error": {"message": "You exceeded your current quota", "type": "insufficient_quota"}}, "quota"),
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
    ev = [{"EventName": "ChampionKill", "EventTime": 500.0, "VictimName": "Moi", "KillerName": "Zed",
           "Assisters": ["Garen#EUW"]},
          {"EventName": "TurretKilled", "EventTime": 520.0, "TurretKilled": "Turret_T2_L_03_A", "KillerName": "Garen"},
          {"EventName": "DragonKill", "EventTime": 530.0, "DragonType": "Fire", "Stolen": "False",
           "KillerName": "Zed"}]
    g = G(events=ev, gold=1500)
    g.champion_stats = {"attackDamage": 75.123, "abilityPower": 210.0, "currentHealth": 800.0, "maxHealth": 1500.0}
    ctx = {"wp": 55, "carte": {"appel": "Recule vers ta tour", "pourquoi": "Zed est niveau 6"},
           "causes": [[500, "Recule plus tôt : mort à 1 contre 2"]], "ph": "voie"}
    snap = ai.build_snapshot(g, moment="death", item_text="Prochain objet : Zhonya", context=ctx,
                             trend="-900 en 2 min")
    me = snap["me"]
    assert me["c"] == "Ahri" and me["g"] == 1500 and me["r"] == "mid" and me["it"] and me["pv"] == "53%"
    assert me["ig"] > 0
    assert snap["en"][0].startswith("Zed mid 9 0/0/1")
    assert snap["morts"] == [{"t": "8:20", "par": "Zed", "aide": ["Garen"], "cause": "Recule plus tôt : mort à 1 contre 2"}]
    assert any("tour détruit (la leur)" in e for e in snap["ev"]) and any("dragon Fire pour eux" in e for e in snap["ev"])
    assert snap["wp"] == 55 and snap["eq"]["tend"] == "-900 en 2 min" and snap["ph"] == "voie"
    keys = list(snap)
    assert keys.index("t") < keys.index("carte") < keys.index("me") < keys.index("en") < keys.index("wp")
    prompt = ai.build_prompt(snap)
    assert "Recule vers ta tour" in prompt and "carte=differe" in prompt
    assert "Moi#EUW" not in prompt and "EUW" not in prompt
    json.loads(prompt.split("minimap) : ", 1)[1].split("\n", 1)[0])
    assert "t=temps de jeu" in ai.system_prompt() and "impératif" in ai.system_prompt()
    assert ai.build_snapshot(g, context=ctx) == ai.build_snapshot(g, context=ctx)      # deterministic


def test_snapshot_size_limit():
    events = [{"EventName": "ChampionKill", "EventTime": 100.0 + i, "VictimName": "Zed", "KillerName": "Moi",
               "Assisters": ["Garen"]} for i in range(60)]
    big = {"plus": {f"fait{i}": "x" * 70 for i in range(40)}, "jgl": {"txt": "y" * 100},
           "coups": ["12:00 blunder : " + "z" * 60] * 3, "balises": ["rivière"] * 2}
    snap = ai.build_snapshot(G(events=events), context=big, limit=1500)
    assert len(json.dumps(snap, ensure_ascii=False, separators=(",", ":")).encode()) <= 1500
    assert snap["me"]["c"] == "Ahri"


def test_compact_values():
    import enum
    from dataclasses import dataclass

    class E(enum.Enum):
        A = "a"

    @dataclass
    class D:
        x: float
        e: E
        _p: int = 1
        icon: object = None

    assert ai.compact({"d": D(1.23456, E.A), "n": float("nan"), "l": list(range(30))}) == \
        {"d": {"x": 1.23, "e": "a"}, "l": list(range(10))}


def test_engine_context_with_fake_engine():
    from types import SimpleNamespace as NS

    tr = NS(alias="Zed", last_seen=95.0, position=lambda: (0.5, 0.5))
    tracker = NS(me=lambda: NS(position=lambda: (0.1, 0.9)), allies=lambda visible_only=False: [],
                 enemies=lambda visible_only=False: [tr], get=lambda alias: tr if alias == "Zed" else None)
    call = NS(text="Recule vers ta tour : Zed est niveau 6", why="Il a son ultime, pas toi.")
    game = G()
    game.enemy_jungler = lambda: game.enemies[0]
    eng = NS(_clock=lambda: 100.0, _game=game, _tracker=tracker, jungler_status_text=lambda: "Jungler vu bot",
             _tactics=NS(macro_active=lambda: call, phase=lambda: "laning", map_state=lambda: None),
             win_probability=lambda: 0.61)
    ctx = ai.engine_context(eng)
    assert ctx["carte"] == {"appel": call.text, "pourquoi": call.why} and ctx["ph"] == "voie"
    assert ctx["jgl"]["c"] == "Zed" and ctx["jgl"]["vu"].endswith("il y a 5 s") and ctx["wp"] == 61
    assert ctx["compo"]["nous"].startswith("dégâts") and "courbe" in ctx["compo"]["eux"]
    assert ai.engine_context(None) == {} and isinstance(ai.engine_context(object()), dict)


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
    assert adv.update(200.0, G(gt=1000.0, level=16)) and adv.wait()      # mid-game slot, quota error
    assert adv.budget_info()["auto_used"] == 1                # failed call refunded
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


def test_manual_ask():
    calls = []

    def caller(prov, key, model, system, prompt, timeout, url):
        calls.append(prompt)
        if len(calls) == 2:
            raise ai.AIError("offline")
        return "Va mid."

    adv = ai.AIAdvisor(SimpleNamespace(ai_provider="ollama", ai_api_key="", ai_model=""), caller=caller,
                       clock=lambda: 0.0)
    assert "Pas de partie" in adv.ask(0.0, GameInfo())
    assert adv.ask(0.0, G(gt=30.0), context=lambda: {"wp": 40}) == "Question envoyée à l'IA…"
    adv.wait()
    res = adv.poll()
    assert res.moment == "manual" and res.text == "Va mid." and not res.error
    assert '"wp":40' in calls[0] and "demande un conseil" in calls[0]
    assert "Patiente" in adv.ask(5.0, G())
    adv.ask(30.0, G())
    adv.wait()
    res = adv.poll()
    assert res.error and "Ollama" in res.text
    off = ai.AIAdvisor(SimpleNamespace(ai_provider="off"))
    assert "désactivé" in off.ask(0.0, G())


@pytest.mark.parametrize("provider", list(ai.PROVIDERS))
def test_postgame_review_and_html(server, provider, tmp_path):
    long_text = "Point fort : ta vision. Axe 1 : farme. Axe 2 : morts. Axe 3 : objectifs. Exercice : 10 min."
    payload = json.loads(json.dumps(RESPONSES[provider], ensure_ascii=False).replace("Achète Zhonya. Va mid.", long_text)
                         .replace("Achète **Zhonya**. Puis joue le dragon.", long_text))
    server.payload = payload
    from treeaicoach.analysis import analyze_game

    rec = json.loads((Path(__file__).parent / "fixtures" / "game_record_sample.json").read_text(encoding="utf-8"))
    cfg = SimpleNamespace(ai_provider=provider, ai_api_key="k", ai_model="")
    review = ai.postgame_review(cfg, analyze_game(rec), url=server.url(PATHS[provider]))
    assert review == long_text                                     # not cut to 2 sentences
    req = server.requests[-1]["body"]
    sent = json.dumps(req, ensure_ascii=False)
    assert "Analyse de la partie (JSON)" in sent and len(sent.encode()) < 40000
    html = tmp_path / "r.html"
    html.write_text("<html><body><h1>Rapport</h1></body></html>", encoding="utf-8")
    assert ai.append_review_html(html, review + "\n<b>x</b>", provider)
    doc = html.read_text(encoding="utf-8")
    assert "Revue de l'IA" in doc and doc.index("ai-review") < doc.index("</body>") and "&lt;b&gt;" in doc
    assert ai.postgame_review(SimpleNamespace(ai_provider="off"), {}) is None
    assert ai.postgame_review(cfg, {}, url="http://127.0.0.1:9/x") is None


def test_check_connection(server):
    server.payload = RESPONSES["anthropic"]
    ok, msg = ai.check_connection(SimpleNamespace(ai_provider="anthropic", ai_api_key="k", ai_model=""),
                                  url=server.url("/v1/messages"))
    assert ok and "Connexion OK" in msg
    ok, msg = ai.check_connection(SimpleNamespace(ai_provider="off"))
    assert not ok
    ok, msg = ai.check_connection(SimpleNamespace(ai_provider="groq", ai_api_key="", ai_model=""))
    assert not ok and "clé" in msg


def test_model_not_found_autopick():
    s = _Server(404, {"error": {"message": "The model `x` does not exist", "code": "model_not_found"}})
    s.get_payload = {"data": [{"id": "whisper-large-v3"}, {"id": "allam-2-7b"}, {"id": "qwen/qwen3-32b"},
                              {"id": "openai/gpt-oss-20b"}]}
    try:
        ai._auto_model.clear()
        with pytest.raises(ai.AIError):        # still 404 for every model in this fake
            ai.call_llm("groq", "k", "x", "s", "p", url=s.url("/openai/v1/chat/completions"))
        gets = [r for r in s.requests if r.get("method") == "GET"]
        assert gets and gets[0]["path"] == "/openai/v1/models"
        posts = [r["body"]["model"] for r in s.requests if r.get("method") != "GET"]
        assert posts == ["x", "openai/gpt-oss-20b"]
    finally:
        ai._auto_model.clear()
        s.close()
    assert ai.pick_model("groq", ["allam-2-7b", "openai/gpt-oss-120b", "qwen/qwen3-32b"]) == "openai/gpt-oss-120b"
    assert ai.pick_model("groq", ["whisper-large-v3", "llama-guard-4"]) is None


def test_grounding_candidates_and_validation():
    from treeaicoach.live_client import parse_allgamedata

    g = parse_allgamedata(json.loads((Path(__file__).parent / "fixtures" / "allgamedata_sample.json")
                                     .read_text(encoding="utf-8")))
    cands = ai.candidate_items(g)
    assert cands and all({"n", "po", "pourquoi"} <= set(c) for c in cands)
    snap = ai.build_snapshot(g, moment="base")
    assert snap["objets_possibles"] == cands[:6]
    assert "objets_possibles" in ai.system_prompt()
    ok = f"Achète {cands[0]['n']} puis prends le Baron."
    assert ai.validate_item_advice(ok, g, cands)
    assert not ai.validate_item_advice("Achète Cris du crépuscule pour survivre.", g, cands)
    assert not ai.validate_item_advice("Finis ton Couperet noir avant le dragon.", g, cands)   # already owned
    assert ai.validate_item_advice("Groupe mid et prends le Héraut.", g, cands)

    def caller(*a, **k):
        return "Achète Cris du crépuscule. Va mid."

    adv = ai.AIAdvisor(SimpleNamespace(ai_provider="groq", ai_api_key="k", ai_model=""), caller=caller)
    adv.ask(0.0, g, item_text="Prochain objet : Gage de Sterak.")
    adv.wait()
    assert adv.poll().text == "Prochain objet : Gage de Sterak."            # itemization fallback


def _real_key():
    """LIVE test, opt-in only: ``TREEAICOACH_LIVE_AI=1`` + ``GROQ_API_KEY`` in the environment.
    (It used to read a key file automatically: every test run spent real requests.)"""
    import os

    return os.environ.get("GROQ_API_KEY", "") if os.environ.get("TREEAICOACH_LIVE_AI") == "1" else ""


@pytest.mark.skipif(not _real_key(), reason="live AI test: set TREEAICOACH_LIVE_AI=1 and GROQ_API_KEY")
def test_real_groq_end_to_end():
    from treeaicoach.live_client import parse_allgamedata

    g = parse_allgamedata(json.loads((Path(__file__).parent / "fixtures" / "allgamedata_sample.json")
                                     .read_text(encoding="utf-8")))
    snap = ai.build_snapshot(g, moment="base", item_text="Prochain objet : Gage de Sterak.")
    raw = ai.call_llm("groq", _real_key(), "", ai.system_prompt(), ai.build_prompt(snap), timeout=15.0,
                      json_mode=True, max_tokens=ai.PLAN_MAX_TOKENS)
    plan = ai.parse_plan(raw)
    assert plan is not None and plan["plan"]
    print("GROQ:", ai.plan_text(plan), "| valid:", ai.validate_item_advice(ai.plan_text(plan), g, snap["objets_possibles"]))


# ------------------------------------------------------------------------------ comeback triggers
def _kill(i, t, victim, killer="Zed"):
    return {"EventID": i, "EventName": "ChampionKill", "EventTime": t, "VictimName": victim, "KillerName": killer}


def test_comeback_scenarios():
    sb = SimpleNamespace(players=(1,), team_gold_diff=-500, fed=(), my_matchup=None)
    d = ai.ComebackDetector()
    assert d.update(0.0, G(600.0), sb) is None
    sb.team_gold_diff = -2300
    assert d.update(1.0, G(610.0), sb) == "gold"
    assert d.update(2.0, G(611.0), sb) is None                       # once per 2k tier
    # win probability drop > 15 pts within 3 min
    d2 = ai.ComebackDetector()
    d2.update(0.0, G(), win_prob=0.55)
    assert d2.update(100.0, G(), win_prob=0.38) == "wp_drop"
    # lost teamfight: 2 allied deaths in 20 s
    ev = [_kill(1, 590.0, "Garen"), _kill(2, 598.0, "Moi")]
    assert ai.ComebackDetector().update(0.0, G(600.0, events=ev)) == "teamfight"
    # my death streak (2 deaths in 4 min, far apart)
    ev = [_kill(1, 400.0, "Moi"), _kill(2, 590.0, "Moi")]
    assert ai.ComebackDetector().update(0.0, G(600.0, events=ev)) == "death_streak"
    # enemy laner fed vs me
    sbf = SimpleNamespace(players=(1,), team_gold_diff=0, fed=("Zed",),
                          my_matchup=SimpleNamespace(enemy_alias="Zed"))
    assert ai.ComebackDetector().update(0.0, G(), sbf) == "lane_fed"
    # objectives drought: enemies take 2 objectives in 4 min, we take none
    ev = [{"EventID": 1, "EventName": "DragonKill", "EventTime": 450.0, "KillerName": "Zed"},
          {"EventID": 2, "EventName": "TurretKilled", "EventTime": 560.0, "TurretKilled": "Turret_T1_L_03_A"}]
    assert ai.ComebackDetector().update(0.0, G(600.0, events=ev)) == "objectives"
    # windows: enemy carries dead with long timers / ace / Baron with numbers
    dead = [P("Jinx", "CHAOS", pos="BOTTOM", dead=True), P("Zed", "CHAOS", pos="MIDDLE", dead=True)]
    for p in dead:
        p.respawn_timer = 40.0
    assert ai.ComebackDetector().update(0.0, G(enemies=dead)) == "carries_dead"
    baron = SimpleNamespace(key="baron", alive=True, remaining=0.0)
    d3 = ai.ComebackDetector()
    d3._last["carries_dead"] = 0.0
    assert d3.update(1.0, G(enemies=dead), objectives=[baron]) == "numbers"
    ace = [{"EventID": 9, "EventName": "Ace", "EventTime": 595.0, "AcingTeam": "ORDER"}]
    assert ai.ComebackDetector().update(0.0, G(600.0, events=ace)) == "ace"


def test_comeback_call_prompt_title_and_guards():
    prompts = []

    def caller(prov, key, model, system, prompt, timeout, url):
        prompts.append(prompt)
        return "Joue côté bot avec ton jungler et échange le dragon contre la tour du haut."

    cfg = SimpleNamespace(ai_provider="groq", ai_api_key="k", ai_model="")
    adv = ai.AIAdvisor(cfg, caller=caller)
    ev = [_kill(1, 590.0, "Garen"), _kill(2, 598.0, "Moi#EUW")]
    adv.update(0.0, G(600.0))
    assert not adv.update(1.0, G(600.0, events=ev), in_fight=True)        # never during a fight
    adv2 = ai.AIAdvisor(cfg, caller=caller)
    adv2.update(0.0, G(600.0))
    assert not adv2.update(1.0, G(600.0, events=ev), threat=1)             # never during a gank
    adv3 = ai.AIAdvisor(cfg, caller=caller)
    adv3.update(0.0, G(600.0))
    assert adv3.update(1.0, G(600.0, events=ev)) and adv3.wait()
    res = adv3.poll()
    assert res.moment == "comeback:teamfight" and res.title == "IA : Plan pour revenir"
    assert "Mode : redresser" in prompts[-1] and '"mode":"redresser"' in prompts[-1]
    assert ai.Advice("x", "comeback:ace", 0.0).title == "IA : Fenêtre à saisir"
    assert ai.Advice("x", "base", 0.0).title == "CONSEIL IA"


def test_budget_five_auto_plus_one_urgent_and_manual_apart():
    cfg = SimpleNamespace(ai_provider="groq", ai_api_key="k", ai_model="")
    adv = ai.AIAdvisor(cfg, caller=lambda *a, **k: "Achète Zhonya.")
    t = [0.0]

    def step(game, **kw):
        t[0] += 100.0
        ok = adv.update(t[0], game, **kw)
        adv.wait()
        adv.poll()
        return ok

    step(G(gt=300.0))
    assert step(G(gt=300.0, gold=1200), in_base=True)                  # 1st base with gold
    assert not step(G(gt=320.0, gold=0))
    assert not step(G(gt=330.0, gold=1500), in_base=True)               # 2nd base early: no slot
    assert step(G(gt=400.0, dead=True))                                # lost fight
    assert not step(G(gt=420.0, level=11))                             # early level-up: skipped
    assert step(G(gt=1000.0, level=16))                                # mid-game
    baron = SimpleNamespace(key="baron", name="Baron", alive=False, remaining=60.0, next_spawn=1500.0)
    assert step(G(gt=1440.0), objectives=[baron])                      # pre-Baron
    assert step(G(gt=1800.0, dead=True))                               # late game
    assert adv.budget_info()["auto_used"] == 5 and ai.budget_text(adv.budget_info()) == "IA 5/5"
    assert not step(G(gt=1900.0, dead=False, level=18, gold=2000), in_base=True)   # budget exhausted
    ev = [_kill(1, 1990.0, "Garen"), _kill(2, 1995.0, "Moi#EUW")]
    assert step(G(gt=2000.0, events=ev))                               # bonus "urgence" (lost big fight)
    info = adv.budget_info()
    assert info["urgent_used"] == 1 and ai.budget_text(info) == "IA 5/5 +1"
    ev2 = ev + [_kill(3, 2290.0, "Garen"), _kill(4, 2295.0, "Moi#EUW")]
    assert not step(G(gt=2300.0, events=ev2))                          # only one bonus
    assert adv.ask(t[0] + 100.0, G(gt=2400.0)) == "Question envoyée à l'IA…"
    adv.wait()
    assert adv.budget_info()["manual"] == 1 and adv.budget_info()["auto_used"] == 5
    adv.reset()
    assert adv.budget_info()["auto_used"] == 0 and ai.budget_text(None) == ""


def test_expert_level_only_urgent_auto_calls():
    cfg = SimpleNamespace(ai_provider="groq", ai_api_key="k", ai_model="", skill_level="expert")
    adv = ai.AIAdvisor(cfg, caller=lambda *a, **k: "Joue avec ton équipe.")
    adv.update(100.0, G(gt=300.0))
    assert not adv.update(200.0, G(gt=300.0, gold=1200), in_base=True)      # 1st base: skipped (expert)
    assert adv.budget_info()["auto_used"] == 0
    ev = [_kill(1, 1990.0, "Garen"), _kill(2, 1995.0, "Moi#EUW")]
    assert adv.update(400.0, G(gt=2000.0, events=ev))                        # urgent: still allowed
    adv.wait()
    assert adv.budget_info()["urgent_used"] == 1
    adv.apply_config(SimpleNamespace(ai_provider="groq", ai_api_key="k", ai_model="", skill_level="avance"))
    assert not adv._urgent_only


def test_postgame_review_is_grounded_in_the_real_build_and_french_items():
    """Real game report: the Groq review advised "Tiamat -> Titanic Hydra ... Sterak Gage avant le
    premier rappel" (English, off-meta, impossible) for Garen. One request; the prompt carries the
    final build + allowed French item names; sentences naming other items are dropped, English
    names of allowed items become French, an English answer is rejected."""
    analysis = {"summary": {"champion": "Garen", "champion_name": "Garen", "position": "TOP", "kills": 1,
                            "deaths": 15, "assists": 5, "cs_per_min": 4.1, "items": [3047, 6631, 3071, 1055],
                            "enemies": [{"alias": "Vladimir", "name": "Vladimir"}, {"alias": "Kindred", "name": "Kindred"},
                                        {"alias": "Ekko", "name": "Ekko"}, {"alias": "Seraphine", "name": "Séraphine"},
                                        {"alias": "Thresh", "name": "Thresh"}]},
                "death_verdicts": {"duel": 4, "late": 3, "missed": 2}, "alert_lead": {"n": 4, "mean": 2.5}}
    calls = []
    hallucinated = ("Bon point : tu as survécu à 10 ganks sur 14. Axe 1 : farme mieux, 4,1 CS/min. "
                    "Conseil d'objets : commence avec le Tiamat → Titanic Hydra pour plus de dégâts, puis achète "
                    "un Sterak Gage avant le premier rappel. Contre Vladimir, prends Sterak's Gage en 3e objet.")

    def caller(prov, key, model, system, prompt, **kw):
        calls.append((system, prompt))
        return hallucinated

    cfg = SimpleNamespace(ai_provider="groq", ai_api_key="k", ai_model="")
    review = ai.postgame_review(cfg, analysis, caller=caller)
    assert len(calls) == 1
    system, prompt = calls[0]
    assert "Objets autorisés" in prompt and "Estropieur" in prompt and "Gage de Sterak" in prompt
    assert "Build final du joueur : Coques en acier, Estropieur, Couperet noir" in prompt
    assert "Hydre titanesque" not in prompt and "français" in system and "trop tard" in system
    assert "4 1v1 perdu" in prompt and "3 alerte trop tardive" in prompt and "2.5 s" in prompt
    assert review is not None
    for bad in ("Titanic", "Hydra", "Tiamat", "Sterak Gage", "Sterak's"):
        assert bad not in review, bad
    assert "prends Gage de Sterak en 3e objet" in review           # allowed item, French name
    assert "Objets : garde Coques en acier et vise" in review      # rule-based replacement of the dropped one
    assert "10 ganks sur 14" in review
    # an English answer never reaches the report
    english = "You should build the Titanic Hydra first and then you should buy Sterak's Gage with your gold before the next fight."
    assert ai.postgame_review(cfg, analysis, caller=lambda *a, **k: english) is None
