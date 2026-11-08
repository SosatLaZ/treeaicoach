"""WoW-expert AI context (treeaicoach/wow/ai_context.py) + the engine routing in WoW mode."""
from __future__ import annotations

import json
from types import SimpleNamespace

from treeaicoach import ai_advisor
from treeaicoach.wow import ai_context as ac

DB = {
    "version": 1,
    "chars": {
        "Fakeone-Fakerealm": {"class": "Chasseur", "level": 26, "zone": "Contreforts de Hautebrande",
                              "updated": 200, "money": 123456, "xp": 5000, "xpMax": 20000, "rested": 4000,
                              "spec": {"Maîtrise des bêtes": 11, "Précision": 5, "Survie": 0},
                              "bags": {"free": 3, "total": 64}, "ammo": 412,
                              "pet": {"family": "Sanglier", "level": 25, "happiness": 2},
                              "quests": [{"title": "Les ours de Hautebrande", "level": 24,
                                          "objectives": [{"text": "Ours tués : 4/8", "done": False},
                                                         {"text": "Griffes : 6/6", "done": True}]},
                                         {"title": "Livraison", "complete": 1}, "Quête simple"],
                              "group": [{"name": "Fakefriend", "class": "Mage", "level": 25,
                                         "zone": "Moulin-de-Tarren"}]},
        "Fakeold-Fakerealm": {"class": "Mage", "level": 5, "updated": 10},
    },
}


def test_current_character_most_recent():
    assert ac.current_character(DB)["class"] == "Chasseur"
    assert ac.current_character({"char": {"level": 3}}) == {"level": 3}
    assert ac.current_character({"level": 7, "class": "Voleur"})["level"] == 7
    assert ac.current_character(None) is None and ac.current_character({"x": 1}) is None


def test_money_text():
    assert ac.money_text(123456) == "12 po 34 pa 56 pc"
    assert ac.money_text(10000) == "1 po"
    assert ac.money_text(0) == "0 pc"
    assert ac.money_text("bad") is None and ac.money_text(-5) is None


def test_character_context_fields_and_no_names():
    ctx = ac.character_context(DB)
    assert ctx["classe"] == "Chasseur" and ctx["niveau"] == 26 and ctx["or"] == "12 po 34 pa 56 pc"
    assert ctx["xp"] == "25 %" and ctx["repos"] == "20 % d'un niveau"
    assert ctx["sacs"] == "3 libres sur 64" and ctx["spe"].startswith("11/5/0")
    assert ctx["familier"]["happiness"] == 2 and ctx["munitions"] == "412"
    assert ctx["quetes"][0] == "Les ours de Hautebrande [24] : Ours tués : 4/8"
    assert ctx["quetes"][1] == "Livraison (terminée)" and ctx["quetes"][2] == "Quête simple"
    assert ctx["groupe"] == ["Mage 25 (Moulin-de-Tarren)"]
    assert "Fakefriend" not in json.dumps(ctx, ensure_ascii=False)


def test_garbage_never_raises():
    for junk in (None, 3, "x", [], {"chars": [1, None, {"quests": "x", "bags": 5, "spec": [1, 2], "level": 1}]},
                 {"char": {"quests": {"a": {"title": "Q", "objectives": {"1": "Tuer"}}}, "money": float("nan")}}):
        assert isinstance(ac.character_context(junk), dict)
    assert ac.build_context(SimpleNamespace(), db={"char": {"level": 2}}, facts=["Familier mort"]) == {
        "niveau": 2, "combat_recent": ["Familier mort"]}


def test_build_prompt():
    p = ac.build_prompt("Où aller au niveau 27 ?", {"niveau": 26}, who="Paul")
    assert p.startswith("Question posée par Paul : Où aller au niveau 27 ?") and '"niveau":26' in p
    assert "/reload" in ac.build_prompt("", {})
    assert ac.DEFAULT_QUESTION in ac.build_prompt("", {})


def test_engine_fallbacks(monkeypatch):
    monkeypatch.setattr(ac, "_lookup", lambda *a: None)          # no wow package functions
    eng = SimpleNamespace(_wow_mode=True, _wow_db={"char": {"level": 9}},
                          _wow_coach=SimpleNamespace(recent_facts=lambda n: [{"text": "Elite engagé"}, "x"]))
    assert ac.wow_mode_active(eng) is True
    assert ac.wow_mode_active(SimpleNamespace()) is False
    assert ac.wow_mode_active(SimpleNamespace(wow_mode=lambda: 1 / 0)) is False
    assert ac.load_db(eng) == {"char": {"level": 9}}
    assert ac.live_facts(eng) == ["Elite engagé", "x"]
    assert ac.build_context(eng)["niveau"] == 9


def test_lookup_prefers_package_flag(monkeypatch):
    import types
    fake = types.ModuleType("treeaicoach.wow.savedvars")
    fake.load_treeai_db = lambda: {"TreeAIDB": {"char": {"level": 11}}}
    monkeypatch.setitem(__import__("sys").modules, "treeaicoach.wow.savedvars", fake)
    monkeypatch.setitem(ac._DB_CACHE, "t", -1e9)
    assert ac.load_db(None, now=0.0) == {"char": {"level": 11}}


def test_wow_answer_uses_wow_prompt(monkeypatch):
    seen = {}

    def fake(provider, key, model, system, prompt, **kw):
        seen.update(system=system, prompt=prompt, kw=kw)
        return "Va aux Paluns."
    monkeypatch.setattr(ai_advisor, "call_llm", fake)
    monkeypatch.setattr(ai_advisor.DAILY_QUOTA, "add", lambda n: None)
    assert ac.wow_answer("groq", "k", "m", "Où aller ?", ctx={"niveau": 26}) == "Va aux Paluns."
    assert "World of Warcraft" in seen["system"] and "Où aller ?" in seen["prompt"]
    assert seen["kw"]["max_tokens"] == 220


def _engine(wow: bool):
    from treeaicoach import engine_coaching as ec
    cls = next(v for v in vars(ec).values() if isinstance(v, type) and "_general_question" in vars(v))
    eng = cls.__new__(cls)
    eng._wow_on = lambda: wow
    eng._cfg = SimpleNamespace(ai_provider="groq", ai_api_key="k", ai_model="m")
    eng.calls, eng.said = [], []
    eng._call = lambda *a: eng.calls.append(a)
    eng._say = lambda t, lvl: eng.said.append(t)
    eng._toast = lambda *a: None
    eng._clock = lambda: 1.0
    eng._listen_chip = lambda *a: None
    return eng


class _SyncThread:
    def __init__(self, target=None, **kw):
        self.target = target

    def start(self):
        self.target()


def test_engine_routes_discord_and_hotkey_in_wow_mode(monkeypatch):
    from treeaicoach import engine_coaching as ec
    monkeypatch.setattr(ec.threading, "Thread", _SyncThread)
    got = []
    monkeypatch.setattr(ac, "wow_answer",
                        lambda p, k, m, q, **kw: got.append((q, kw.get("who"))) or "Réponse WoW.")
    eng = _engine(True)
    eng._discord_voice_question("Paul", "Quelle zone ?")
    assert got[-1] == ("Quelle zone ?", "Paul") and eng.calls[-1] == ("ai_answer", "Réponse WoW.")
    assert eng.ask_ai() == "Question envoyée à l'IA…"
    assert got[-1][0] == ac.DEFAULT_QUESTION and eng.said[-1] == "Réponse WoW."


def test_engine_general_question_lol_mode_unchanged(monkeypatch):
    from treeaicoach import engine_coaching as ec
    monkeypatch.setattr(ec.threading, "Thread", _SyncThread)
    monkeypatch.setattr(ai_advisor, "general_answer", lambda *a: "Réponse LoL.")
    monkeypatch.setattr(ac, "wow_answer", lambda *a, **k: "WRONG")
    eng = _engine(False)
    eng._general_question("Bonjour ?")
    assert eng.calls[-1] == ("ai_answer", "Réponse LoL.")
