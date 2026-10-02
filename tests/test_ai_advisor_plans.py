"""ai_advisor.py v2: strict JSON plans, offline rule-based plans, smarter key moments, play ratings."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest
from test_ai_advisor import PATHS, RESPONSES, G, _kill, _Server

from treeaicoach import ai_advisor as ai
from treeaicoach.plays import Play

CFG = NS(ai_provider="groq", ai_api_key="k", ai_model="")
PLAN = {"plan": "Dragon dans 1:20, ta voie a la prio : rentre maintenant.",
        "etapes": ["Pose 2 balises : rivière du bas, pixel bot.", "Sois au pit 20 s avant."],
        "objectif": "dragon", "urgence": "haute"}


def test_parse_plan_strict():
    p = ai.parse_plan("```json\n" + json.dumps(PLAN, ensure_ascii=False) + "\n```")
    assert p["plan"].startswith("Dragon dans 1:20") and len(p["etapes"]) == 2 and p["objectif"] == "dragon"
    assert ai.parse_plan('<think>x</think>{"plan": "Va bot.", "etapes": [], "objectif": "Héraut"}')["objectif"] \
        == "heraut"
    bad = ['{"plan": 3}', '{"etapes": ["a"]}', "pas du json", '{"plan": "Va", "etapes": "a"}', "[1, 2]",
           '{"plan": "Rentre.", "etapes": [1]}', "{plan: 'x'}", ""]
    assert all(ai.parse_plan(b) is None for b in bad)
    long = ai.parse_plan(json.dumps({"plan": "mot " * 100, "etapes": ["x " * 80] * 5, "urgence": "max"}))
    assert len(long["plan"]) <= ai.MAX_PLAN_CHARS + 1 and len(long["etapes"]) == 3 and long["urgence"] is None
    assert all(len(x) <= ai.MAX_STEP_CHARS + 1 for x in long["etapes"])
    txt = ai.plan_text(PLAN)
    assert txt.startswith("Dragon dans 1:20") and "Pose 2 balises" in txt and len(txt) <= ai.MAX_ADVICE_CHARS + 1


@pytest.mark.parametrize("provider", list(ai.PROVIDERS))
def test_json_mode_requests(provider):
    s = _Server(200, json.loads(json.dumps(RESPONSES[provider], ensure_ascii=False)
                                .replace("Achète Zhonya. Va mid.", "{\\\"plan\\\": \\\"Va mid.\\\"}")
                                .replace("Achète **Zhonya**. Puis joue le dragon.", "{\\\"plan\\\": \\\"Va mid.\\\"}")))
    try:
        raw = ai.call_llm(provider, "k", "", "SYS", "P", url=s.url(PATHS[provider]), json_mode=True,
                          max_tokens=ai.PLAN_MAX_TOKENS)
        assert ai.parse_plan(raw)["plan"] == "Va mid."
        b = s.requests[-1]["body"]
        if provider == "gemini":
            assert b["generationConfig"]["responseMimeType"] == "application/json"
        elif provider in ("groq", "openrouter"):
            assert b["response_format"] == {"type": "json_object"}
        elif provider == "ollama":
            assert b["format"] == "json"
    finally:
        s.close()


def _adv(caller, clock=None):
    return ai.AIAdvisor(CFG, caller=caller, clock=clock or (lambda: 0.0))


def test_advisor_uses_json_plan_and_snapshot_has_plays():
    prompts = []

    def caller(prov, key, model, system, prompt, timeout=6.0, url=None, json_mode=False, max_tokens=200):
        prompts.append((prompt, json_mode, max_tokens))
        return json.dumps(PLAN, ensure_ascii=False)

    adv = _adv(caller)
    ev = [_kill(1, 590.0, "Garen"), _kill(2, 598.0, "Moi#EUW")]
    adv.update(0.0, G(600.0))
    ctx = {"coups": [{"c": "blunder", "r": "Mort 3 s après l'alerte de gank.", "t": "9:55"}], "prec": 61,
           "prio": {"bot": "prio (vague chez eux)"}, "balises": ["rivière du bas"]}
    assert adv.update(1.0, G(600.0, events=ev), context=ctx) and adv.wait()
    res = adv.poll()
    assert res.source == "ai" and res.steps and res.goal == "dragon" and "Pose 2 balises" in res.text
    prompt, json_mode, max_tokens = prompts[-1]
    assert json_mode and max_tokens == ai.PLAN_MAX_TOKENS
    assert '"coups"' in prompt and "blunder" in prompt and '"prec":61' in prompt and "objet JSON strict" in prompt


def test_broken_json_falls_back_to_rules():
    adv = _adv(lambda *a, **k: '{"plan": 42, "etapes": "oops"}')
    ev = [_kill(1, 590.0, "Garen"), _kill(2, 598.0, "Moi#EUW")]
    adv.update(0.0, G(600.0))
    assert adv.update(1.0, G(600.0, events=ev)) and adv.wait()
    res = adv.poll()
    assert res is not None and res.source == "rules" and res.title == "PLAN : Plan pour revenir"
    assert "Combat perdu" in res.text and adv.json_errors == 1


def test_offline_fallback_rule_plan_and_backoff():
    now = [0.0]
    calls = []

    def caller(*a, **k):
        calls.append(1)
        raise ai.AIError("offline")

    adv = ai.AIAdvisor(CFG, caller=caller, clock=lambda: now[0])
    adv.update(0.0, G(600.0))
    ev = [_kill(1, 590.0, "Garen"), _kill(2, 598.0, "Moi#EUW")]
    assert adv.update(1.0, G(600.0, events=ev)) and adv.wait()
    res = adv.poll()
    assert res.source == "rules" and not res.error and adv.budget_info()["auto_used"] == 0
    assert "injoignable" in adv.status()[1]
    # still offline (back-off): the next plan moment gets a rule plan without any request
    baron = NS(key="baron", name="Baron", alive=False, remaining=85.0, next_spawn=1500.0)
    n = len(calls)
    adv.update(200.0, G(1415.0))
    assert not adv.update(201.0, G(1415.0), objectives=[baron])
    assert len(calls) == n
    res = adv.poll()
    assert res is not None and res.source == "rules" and "Baron dans 1:25" in res.text
    assert res.title == "PLAN : Objectif"


def test_rule_plan_cases():
    snap = {"me": {"c": "Jinx", "r": "adc"}, "prio": {"bot": "prio (vague chez eux)"},
            "balises": ["rivière du bas", "buisson du dragon"], "objt": [["Dragon", 80], ["Baron", 400]],
            "diff": {"eq": -500}}
    p = ai.rule_plan("objective", snap)
    assert p["plan"] == "Dragon dans 1:20 : ta voie a la prio, rentre maintenant et va te placer."
    assert p["etapes"][0] == "Pose 2 balises : rivière du bas, buisson du dragon." and p["objectif"] == "dragon"
    snap2 = dict(snap, prio={"bot": "vague chez nous"})
    assert "pousse ta vague" in ai.rule_plan("objective", snap2)["plan"]
    win = dict(snap, en=[{"c": "Zed", "rs": 35}, {"c": "Jinx", "rs": 28}, {"c": "Lux"}],
               objt=[["Baron", 0], ["Dragon", 0]])
    w = ai.rule_plan("comeback:carries_dead", win)
    # V2 audit: 2 dead for 28 s is a dragon, not a Baron (walk + kill > 28 s)
    assert w["plan"].startswith("2 ennemis morts (Zed, Jinx) pendant 28 s : prends le dragon")
    win3 = dict(win, en=[{"c": "Zed", "rs": 35}, {"c": "Jinx", "rs": 38}, {"c": "Lux"}])
    assert ai.rule_plan("comeback:carries_dead", win3)["plan"].startswith(
        "2 ennemis morts (Zed, Jinx) pendant 35 s : prends le Baron")
    behind = {"me": {"r": "top"}, "diff": {"eq": -4200, "vs": "Darius"}, "jgl": "Vi vue en haut il y a 12 s",
              "objt": [["Héraut", 60]]}
    b = ai.rule_plan("comeback:gold", behind)
    assert "Retard de 4 200 PO" in b["plan"] and "Vi vue en haut" in b["etapes"][0]
    assert ai.rule_plan("comeback:lane_fed", behind)["plan"].startswith("Darius est trop fort")
    assert ai.rule_plan("level", {}) is None and ai.rule_plan("base", None) is None
    for plan in (p, w, b):
        assert "—" not in ai.plan_text(plan)


def test_smarter_moments_swing_lead_trade_blunders_and_objective_lead():
    sb = NS(players=(1,), team_gold_diff=0, fed=(), my_matchup=None)
    d = ai.ComebackDetector()
    assert d.update(0.0, G(600.0), sb) is None
    sb.team_gold_diff = -1600
    assert d.update(30.0, G(630.0), sb) is None             # too fast to be a swing (and < 2k tier)
    assert d.update(90.0, G(690.0), sb) == "swing"
    d2 = ai.ComebackDetector()
    sb2 = NS(players=(1,), team_gold_diff=-1000, fed=(), my_matchup=None)
    d2.update(0.0, G(600.0), sb2)
    sb2.team_gold_diff = 800
    assert d2.update(100.0, G(700.0), sb2) == "lead" and "lead" in ai.WINDOW_REASONS
    # 2 allied deaths traded against 2 enemy deaths: not a lost fight
    ev = [_kill(1, 590.0, "Garen"), _kill(2, 598.0, "Moi#EUW"), _kill(3, 595.0, "Zed", "Garen"),
          _kill(4, 596.0, "Zed", "Garen")]
    g = G(600.0, events=ev)
    assert ai.ComebackDetector().update(0.0, g) != "teamfight"
    # objective moment fires in the 60..100 s window (plan time); Atakhan (removed in 26.1) never does
    md = ai.MomentDetector()
    md.update(G())
    atk = NS(key="atakhan", name="Atakhan", alive=False, remaining=95.0, next_spawn=1200.0)
    assert md.update(G(), objectives=[atk]) is None
    baron = NS(key="baron", name="Baron", alive=False, remaining=95.0, next_spawn=1200.0)
    assert md.update(G(), objectives=[baron]) == "objective" and md.last_objective == "baron"
    # dragon = major objective (budget slot) only from 14:00
    b = ai.AIBudget()
    assert b.pick("objective", 600.0, "dragon") is None
    assert b.pick("objective", 900.0, "dragon") == "objective"
    # two blunders in 4 min -> comeback:blunders
    adv = _adv(lambda *a, **k: json.dumps(PLAN))
    adv.update(0.0, G(600.0))
    adv.note_play(Play("blunder", "death_after_warning", "x", 10.0, 610.0, "a"))
    adv.note_play(Play("great", "solo_kill", "x", 20.0, 620.0, "b"))
    assert not adv.update(20.0, G(620.0))
    adv.note_play(Play("mistake", "death_gold", "x", 100.0, 700.0, "c"))
    assert adv.update(120.0, G(720.0)) and adv.wait()
    assert adv.poll().moment == "comeback:blunders"


def test_engine_context_v2_keys():
    tr_me = NS(position=lambda: (0.8, 0.9))
    tracker = NS(me=lambda: tr_me, allies=lambda visible_only=False: [], enemies=lambda visible_only=False: [],
                 get=lambda alias: None)
    sb = NS(players=(1,), team_gold_diff=-1800, ally_kills=4, enemy_kills=7,
            my_matchup=NS(gold_diff=-600, level_diff=-1, cs_diff=-12, enemy="Zed", enemy_alias="Zed"))
    plays_hist = [Play("blunder", "facecheck", "Mort dans le brouillard.", 1.0, 590.0, "k")]
    eng = NS(_clock=lambda: 100.0, _game=G(600.0), _tracker=tracker, scoreboard_summary=lambda: sb,
             _coach=NS(waves=lambda: {"bot": {"state": "pushing"}, "top": {"state": "pushed_in"}}),
             _plays=NS(history=lambda: plays_hist), plays_summary=lambda: {"total": 1, "precision": 58},
             _objectives=NS(states=lambda: [NS(key="dragon", name="Dragon", alive=False, remaining=70.0)]),
             jungle_intel=lambda: {"start": "rouge haut", "vu": "bot 0:45"})
    ctx = ai.engine_context(eng)
    assert ctx["coups"][0].startswith("9:50 blunder") and ctx["prec"] == 58
    assert ctx["diff"] == {"eq": -1800, "kills": "4-7", "po": -600, "niv": -1, "cs": -12, "vs": "Zed", "vs_alias": "Zed"}
    assert ctx["conseils"] and all(isinstance(x, str) for x in ctx["conseils"])        # matchups.json lines
    assert ctx["prio"]["bot"].startswith("prio") and ctx["prio"]["top"] == "vague chez nous"
    assert ctx["jgl"]["start"] == "rouge haut"
    assert ctx["balises"] and all(isinstance(x, str) for x in ctx["balises"])
    snap = ai.build_snapshot(G(600.0), moment="objective", context=ctx,
                             objectives=[NS(key="dragon", name="Dragon", alive=False, remaining=70.0)])
    assert snap["obj"] == [["Dragon", 70]]
    assert snap["voie"]["vs"] == "Zed" and snap["voie"]["po"] == -600 and snap["voie"]["conseils"]
    assert snap["eq"]["po"] == -1800 and snap["eq"]["kills"] == "4-7"
    assert ai.rule_plan("objective", snap)["plan"].startswith("Dragon dans 1:10")


def test_old_plain_callers_still_work():
    adv = _adv(lambda prov, key, model, system, prompt, timeout, url: "Va mid.")
    assert not adv._caller_json
    assert adv.ask(0.0, G(gt=200.0)) == "Question envoyée à l'IA…"
    adv.wait()
    assert adv.poll().text == "Va mid."


def test_jungle_intel_tracker_state_in_context():
    from treeaicoach.jungle_intel import JungleIntel

    st = JungleIntel(alias="Vi", name="Vi", level=7, farming=True, farm_side="bot", text="Vi farme côté bas")
    eng = NS(_clock=lambda: 50.0, _game=G(600.0), _jungle_intel=NS(state=lambda: st))
    ctx = ai.engine_context(eng)
    assert ctx["jgl"] == {"c": "Vi", "txt": "Vi farme côté bas", "farm": "bot", "lv": 7}


def test_hard_cap_of_ten_requests_per_game():
    from treeaicoach.ai_advisor import AIBudget, GAME_HARD_CAP
    b = AIBudget()
    b.manual = GAME_HARD_CAP - 1
    assert not b.exhausted
    b.manual += 1
    assert b.exhausted
    assert b.pick("comeback:gold_swing", 1500.0) is None
    assert b.pick("base", 300.0) is None
    assert b.snapshot()["cap"] == GAME_HARD_CAP == 10
