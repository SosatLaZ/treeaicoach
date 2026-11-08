"""« Hey coach » in WoW mode: the question-aware context (right data, token budget, stale data, no names), the
30-question eval set, the answer without AI, the end-to-end answer path and the engine wiring."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_eval_cases as E  # noqa: E402

from treeaicoach import ai_advisor  # noqa: E402
from treeaicoach.wow import ai_context as ac  # noqa: E402
from treeaicoach.wow import ai_facts as F  # noqa: E402


def ctx_for(q: str, kind: str = "full", **kw) -> dict:
    sc = E.scenario(kind)
    return ac.question_context(q, db=sc["db"], live=sc["live"], plan=sc["plan"], facts=sc["facts"], now=E.NOW, **kw)


def dumped(ctx: dict) -> str:
    return json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))


def _nums(text: str) -> set[str]:
    return {re.sub(r"\s", "", m) for m in re.findall(r"\d[\d  ]*\d|\d", text)}


# ------------------------------------------------------------------ the eval set (30 questions)
def test_eval_set_has_30_cases_covering_every_subject():
    assert len(E.CASES) == 30 and len({c["id"] for c in E.CASES}) == 30
    subjects = {ac.intents(c["q"])[0] for c in E.CASES}
    assert {"next", "quests", "xp", "detour", "dungeon", "ah", "money", "group", "pet", "bestpet", "errors",
            "talents", "gear"} <= subjects
    assert {c["scenario"] for c in E.CASES} == {"full", "stale", "empty", "noah", "duo"}


@pytest.mark.parametrize("case", E.CASES, ids=[c["id"] for c in E.CASES])
def test_eval_case(case):
    ctx = ctx_for(case["q"], case["scenario"])
    s = dumped(ctx)
    for fact in case["facts"]:
        assert fact in s, f"{case['id']}: missing {fact!r} in {s}"
    for bad in list(case.get("absent", [])) + list(E.NAMES):
        assert bad not in s, f"{case['id']}: {bad!r} must not be sent"
    loc = ac.local_answer(case["q"], ctx)
    assert loc, f"{case['id']}: no answer without AI"
    for want in case.get("local", []):
        assert F.fold(want) in F.fold(loc), f"{case['id']}: {want!r} not in local answer {loc!r}"
    # the answer said without AI never carries a number that is not in the facts or the question
    allowed = _nums(s) | _nums(case["q"]) | {"1", "2", "3"}
    assert _nums(loc) <= allowed, f"{case['id']}: invented number(s) {_nums(loc) - allowed} in {loc!r}"
    assert len(loc) <= 400 and "≈" not in loc and "XP/h" not in loc


# ------------------------------------------------------------------ right data for the question
def test_sections_follow_the_question():
    assert list(ctx_for("on fait quoi ?"))[:3] == ["fraicheur", "joueur", "trajet"]
    assert "hv" in ctx_for("je vends quoi ?") and "trajet" not in ctx_for("je vends quoi ?")
    assert "donjon" in ctx_for("quel donjon ce soir ?") and "groupe" in ctx_for("quel donjon ce soir ?")
    assert "calcul_detour" in ctx_for("ça vaut le coup d'aller aux Salines ?")
    assert "erreurs" in ctx_for("l'addon a des erreurs ?") and "hv" not in ctx_for("l'addon a des erreurs ?")
    d = ctx_for(ac.DEFAULT_QUESTION)
    assert ac.intents(ac.DEFAULT_QUESTION) == ["default"] and "alertes" in d and "trajet" in d


def test_intents_routing():
    table = {
        "Hey coach, on fait quoi ?": "next", "où je vais ensuite": "next", "quelle zone pour moi": "next",
        "je vends quoi": "ah", "il y a une affaire à l'HV ?": "ah", "combien j'ai d'or": "money",
        "quel donjon à 2": "dungeon", "on tente les Mortemines ?": "dungeon",
        "ça vaut le coup d'aller aux Salines pour 2 quêtes ?": "detour", "c'est rentable d'aller à Tanaris ?": "detour",
        "mon familier est content ?": "pet", "il me reste des balles ?": "pet",
        "quel familier apprivoiser ?": "bestpet", "pourquoi ça bug ?": "errors", "qui est mort ?": "group",
        "combien d'xp par heure": "xp", "je monte quand ?": "xp", "j'ai des quêtes à rendre ?": "quests",
        "quels talents ?": "talents", "je change d'arme ?": "gear", "quels sorts au niveau 30 ?": "spells",
        "ça va coach ?": "default", "": "default", "c'est rentable de revendre le cuir ?": "ah",
        "je suis mort, je fais quoi ?": "dead", "qui fait le plus de dégâts ?": "group",
    }
    for q, want in table.items():
        assert ac.intents(q)[0] == want, (q, ac.intents(q))


def test_xp_rate_sources_and_confidence():
    x = ctx_for("combien d'xp par heure ?")["xp"]
    assert x["xp_par_heure"] == "21 500" and x["confiance"] == "bonne" and "46 min" in x["source"]
    assert x["niveau_29_dans"] == "≈ 45 min" and x["quetes_pretes"] == "2 à rendre, ≈ 3 950 XP"
    # under 10 min of chat log: no live XP/h, the addon's own measure (fresh) is used, said « moyenne »
    sc = E.scenario("full")
    chat, events = E._chat(E.NOW - 5 * 60, 5, 350)
    x2 = ac.question_context("xp par heure ?", db=sc["db"], live={"chat": chat, "chat_age": 10.0, "events": events},
                             facts=[], plan=None, now=E.NOW)["xp"]
    assert x2["xp_par_heure"] == "20 500" and x2["confiance"] == "moyenne"
    # a level gained in the chat log after the /reload: the level and the XP left follow it
    events.append({"t": E.NOW - 60, "kind": "level", "level": 29})
    events.append({"t": E.NOW - 30, "kind": "xp", "xp": 1000})
    live = {"chat": dict(chat, events=len(events)), "chat_age": 5.0, "events": events}
    c3 = ac.question_context("xp par heure ?", db=sc["db"], live=live, facts=[], plan=None, now=E.NOW)
    assert c3["joueur"]["niveau"] == 29 and "niveau pris en direct" in c3["joueur"]["xp"]
    assert c3["xp"]["xp_restante"] == "42 950"           # 44 300 (29 → 30) − 1 000 − 350 gained after the ding


def test_detour_is_computed_like_a_pro():
    d = ctx_for("Ça vaut le coup d'aller aux Salines pour 2 quêtes ?")["calcul_detour"]
    assert d["destination"] == "Les Salines (Mille pointes)" and d["quetes"] == 2
    assert d["xp_des_quetes"] == "≈ 5 100 XP"                       # 2 400 + 2 700, no decay at level 28
    assert "Vol pour Poste de Librevent" in d["trajet_aller"] and "avec le retour" in d["temps_total"]
    assert d["verdict"].startswith("pas rentable") and "21 500 XP/h" in d["xp_par_heure_ici"]
    assert "precision" not in d                                      # quest positions known: not approximate
    # no measured XP/h at all: compared with killing mobs of his level, said as an estimate (« probablement »)
    sc = E.scenario("full")
    sc["db"]["chars"][E.KEY]["summary"].pop("stats")
    sc["db"]["chars"][E.KEY]["learn"] = {}
    sc["db"]["chars"][E.KEY]["summary"]["plan"]["minutes"] = 5
    d2 = ac.question_context("ça vaut le coup d'aller aux Salines ?", db=sc["db"], live={}, plan=None, facts=[],
                             now=E.NOW)["calcul_detour"]
    assert "estimation" in d2["xp_par_heure_repere"] and d2["verdict"].startswith("probablement")
    # an unknown place: no detour section (the model answers from general knowledge, flagged « d'après Classic »)
    assert "calcul_detour" not in ctx_for("ça vaut le coup d'aller à Bidulecity ?")


def test_ah_rules():
    h = ctx_for("je vends quoi ?")["hv"]
    assert h["scan_hv"] == "il y a 31 min" and h["objets_gris"].startswith("7 (≈ 1 po 18 pa")
    old = E.scenario("full")
    old["db"]["chars"][E.KEY]["summary"]["auction"]["scanAge"] = 30 * 3600     # a scan older than 24 h
    h2 = ac.question_context("une affaire à l'HV ?", db=old["db"], live=old["live"], plan=None, facts=[],
                             now=E.NOW)["hv"]
    assert "affaires_sures" not in h2 and any("24 h" in m for m in h2["manque"])


def test_dungeon_fit_for_horde():
    d = ctx_for("quel donjon on fait ?")["donjon"]
    assert not any("Prison de Hurlevent" in x for x in d["a_ton_niveau"])    # Alliance city
    assert d["a_ton_niveau"][0].startswith("Kraal de Tranchebauge") and "nouveau Forever" in " ".join(d["a_ton_niveau"])
    assert ctx_for("on fait Ragefeu ?")["donjon"]["demande"].startswith("Gouffre de Ragefeu (13–18) : vous êtes au-dessus")


# ------------------------------------------------------------------ token budget
def test_state_respects_the_token_budget():
    sc = E.huge_scenario()
    for q in ("on fait quoi ?", "je vends quoi ?", "mes potes ?", "j'ai des quêtes à rendre ?", ac.DEFAULT_QUESTION,
              "ça vaut le coup d'aller aux Salines pour 2 quêtes ?"):
        ctx = ac.question_context(q, db=sc["db"], live=sc["live"], plan=sc["plan"], facts=sc["facts"], now=E.NOW)
        s = dumped(ctx)
        assert len(s) <= ac.STATE_MAX_CHARS and ac.approx_tokens(s) <= ac.TOKEN_BUDGET, (q, len(s))
        assert "joueur" in ctx
        main = ac._SECTIONS[ac.intents(q)[0]][1]
        assert main in ctx, (q, list(ctx))               # the section the question is about survives the cut
        prompt = ac.build_prompt(q, ctx)
        json.loads(prompt.split("(JSON) : ", 1)[1])      # never cut in the middle
    tight = ac.question_context("on fait quoi ?", db=sc["db"], live=sc["live"], plan=sc["plan"], facts=[],
                                now=E.NOW, budget=900)
    assert len(dumped(tight)) <= 900 and "trajet" in tight


def test_build_prompt_drops_whole_sections_not_half_json():
    big = {f"k{i}": "x" * 900 for i in range(10)}
    p = ac.build_prompt("q", big)
    state = json.loads(p.split("(JSON) : ", 1)[1])
    assert len(json.dumps(state)) <= 2 * ac.STATE_MAX_CHARS + 50 and "k0" in state


# ------------------------------------------------------------------ stale data (> 15 min)
def test_stale_volatile_data_is_dropped_and_the_rest_labelled():
    s = dumped(ctx_for("on fait quoi ?", "stale"))
    assert "il y a 2 h : quêtes et état peut-être dépassés" in s
    ctx = ctx_for("mes potes ?", "stale")
    g = ctx["groupe"]
    assert g["age"] == "il y a 2 h (dernier /reload)" and g["membres"] == ["Prêtre 27", "Guerrier 28", "Mage 27"]
    assert "conseils" not in g and "mort" not in dumped(ctx)
    x = ctx_for("je monte quand ?", "stale")["xp"]
    assert x["age"].startswith("il y a 2 h") and "20 500" not in dumped(x) and "dernières sessions" in x["source"]
    q = ctx_for("j'ai des quêtes à rendre ?", "stale")["quetes"]
    assert q["age"].startswith("il y a 2 h") and q["pretes_a_rendre"]


def test_fresh_sections_carry_no_age_label():
    for q in ("on fait quoi ?", "mes potes ?", "je vends quoi ?", "combien d'xp par heure ?"):
        assert '"age"' not in dumped(ctx_for(q)), q


def test_volatile_section_older_than_15_min_is_excluded_not_labelled():
    snap = F.make_snap(E.scenario("full")["db"], now=E.NOW)
    secs = [F.Section("groupe", {"membres": ["Guerrier 28, mort"]}, age=16 * 60, volatile=True),
            F.Section("quetes", {"journal": "14/20"}, age=16 * 60)]
    snap_q = "on fait quoi ?"
    original = ac._builders

    def fake(snap_, question):
        b = original(snap_, question)
        b["trajet"] = lambda: secs[0]
        b["quetes"] = lambda: secs[1]
        return b
    ac._builders = fake
    try:
        ctx = ac.assemble(snap, snap_q)
    finally:
        ac._builders = original
    s = dumped(ctx)
    assert "Guerrier 28, mort" not in s and any("trop vieux" in m for m in ctx["manque"])
    assert ctx["quetes"]["age"] == "il y a 16 min (dernier /reload)"


# ------------------------------------------------------------------ names
def test_no_player_name_ever_leaves():
    qs = [c["q"] for c in E.CASES] + [ac.DEFAULT_QUESTION, "on fait quoi ensemble ?", "qui fait le plus de dégâts ?"]
    for kind in ("full", "stale", "noah", "duo"):
        for q in qs:
            s = ac.build_prompt(q, ctx_for(q, kind))
            for name in E.NAMES:
                assert name.lower() not in s.lower(), (kind, q, name)
    labels = F.make_snap(E.scenario("full")["db"], now=E.NOW).labels
    assert labels[E.ME] == "toi" and labels["Fakewar"] == "Guerrier 28" and labels[E.GUILD] == "ta guilde"


def test_anonymize_whole_words_only():
    assert F.anonymize("Fakewar et Fakewarrior", {"Fakewar": "Guerrier 28"}) == "Guerrier 28 et Fakewarrior"


# ------------------------------------------------------------------ never invent: missing data
def test_empty_or_unknown_data_says_so():
    ctx = ctx_for("dans combien de temps je suis niveau 29 ?", "empty")
    assert set(ctx) == {"fraicheur", "manque"} and "/reload" in ctx["manque"][0]
    assert ac.local_answer("on fait quoi ?", ctx).startswith("Je n'ai pas encore la donnée")
    assert ac.local_answer("c'est quoi le meilleur talent ?", ctx).startswith("Je n'ai pas encore la donnée")
    # garbage never raises
    for junk in (None, 3, "x", [], {"chars": [1, None, {"summary": {"quests": "x", "level": "?"}}]},
                 {"chars": {"a": {"summary": {"group": {"v": 2, "members": "nope"}, "auction": 5, "hunter": []}}}}):
        assert isinstance(ac.question_context("on fait quoi ?", db=junk, live={}, plan=None, facts=[], now=E.NOW), dict)


# ------------------------------------------------------------------ the end-to-end answer
def test_answer_question_uses_llm_with_grounded_prompt(monkeypatch):
    seen = {}

    def fake(provider, key, model, system, prompt, **kw):
        seen.update(system=system, prompt=prompt, kw=kw, key=key)
        return "Rends ta quête au Poste de Bois-Brisé, ≈ 4 min. Ensuite les Écorchemousses ! Puis XP/h. Et plus."
    monkeypatch.setattr(ai_advisor, "call_llm", fake)
    monkeypatch.setattr(ai_advisor.DAILY_QUOTA, "add", lambda n: None)
    ctx = ctx_for("on fait quoi ?")
    ans = ac.answer_question("groq", "SECRET-KEY-123", "m", "on fait quoi ?", ctx=ctx, who="Paul")
    assert seen["system"] == ac.WOW_PROMPT and seen["kw"]["max_tokens"] == ac.ANSWER_MAX_TOKENS
    assert seen["prompt"].startswith("Question posée par Paul : on fait quoi ?") and "Le cristal de Satyr" in seen["prompt"]
    assert "SECRET-KEY-123" not in seen["prompt"] + seen["system"]
    assert ans.count(".") + ans.count("!") <= 3 and "≈" not in ans and "environ 4 min" in ans


def test_answer_question_without_ai_or_when_it_fails(monkeypatch):
    ctx = ctx_for("on fait quoi ?")
    off = ac.answer_question("off", "", "", "on fait quoi ?", ctx=ctx)
    assert off.startswith("Rends « Le cristal de Satyr »")
    assert ac.answer_question("off", "", "", "raconte une blague", ctx={}) == ac.MSG_AI_OFF

    def boom(*a, **k):
        raise ai_advisor.AIError("offline", "test")
    monkeypatch.setattr(ai_advisor, "call_llm", boom)
    monkeypatch.setattr(ai_advisor.DAILY_QUOTA, "add", lambda n: None)
    assert ac.answer_question("groq", "k", "m", "on fait quoi ?", ctx=ctx).startswith("Rends")
    assert ac.answer_question("groq", "k", "m", "raconte une blague", ctx={}) == ac.MSG_AI_DOWN


def test_quota_exhausted_answers_from_facts(monkeypatch):
    class Q:
        exhausted = True

        def add(self, n):
            raise AssertionError("no request when the quota is exhausted")
    monkeypatch.setattr(ai_advisor, "DAILY_QUOTA", Q())
    monkeypatch.setattr(ai_advisor, "call_llm", lambda *a, **k: pytest.fail("no request"))
    assert "Étoffe de laine" in ac.answer_question("groq", "k", "m", "une affaire à l'HV ?",
                                                    ctx=ctx_for("une affaire à l'HV ?"))


# ------------------------------------------------------------------ live chat log accessor
def test_chat_watch_reads_the_live_chat_log(tmp_path: Path):
    import time as _t
    log = tmp_path / "WoWChatLog.txt"
    t0 = _t.time() - 20 * 60
    lines = []
    for i in range(21):
        tm = _t.localtime(t0 + 60 * i)
        lines.append(f"{tm.tm_mon}/{tm.tm_mday} {tm.tm_hour:02d}:{tm.tm_min:02d}:{tm.tm_sec:02d}.000  "
                     f"Le Écorchemousse meurt, vous gagnez 300 points d'expérience.")
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    eng = SimpleNamespace(_wow_install=SimpleNamespace(chat_log=log))
    live = ac.live_state(eng)
    assert live["chat"]["xp"] == 6300 and live["chat_age"] is not None and len(live["events"]) == 21
    with log.open("a", encoding="utf-8") as fh:
        tm = _t.localtime(_t.time())
        fh.write(f"{tm.tm_mon}/{tm.tm_mday} {tm.tm_hour:02d}:{tm.tm_min:02d}:{tm.tm_sec:02d}.000  "
                 "Vous gagnez 500 points d'expérience.\n")
    assert ac.live_state(eng)["chat"]["xp"] == 6800          # only the new line is read
    assert ac.live_state(SimpleNamespace(_wow_install=None)) == {"chat": {}, "chat_age": None, "events": []}


# ------------------------------------------------------------------ engine wiring (WoW only; LoL untouched)
def _engine():
    from treeaicoach import engine_coaching as ec
    cls = next(v for v in vars(ec).values() if isinstance(v, type) and "_general_question" in vars(v))
    eng = cls.__new__(cls)
    eng._cfg = SimpleNamespace(ai_provider="off", ai_api_key="", ai_model="")
    eng.calls, eng.said = [], []
    eng._call = lambda *a: eng.calls.append(a)
    eng._say = lambda t, lvl: eng.said.append(t)
    eng._toast = lambda *a: None
    eng._clock = lambda: 1.0
    eng._listen_chip = lambda *a: None
    eng._lock = __import__("threading").Lock()
    return eng


class _SyncThread:
    def __init__(self, target=None, **kw):
        self.target = target

    def start(self):
        self.target()


def test_mode_flag_no_longer_shadows_the_wow_check(monkeypatch):
    """wow/mode.py sets ``engine._wow_mode = True``: that used to replace the engine's ``_wow_mode()`` method,
    so every check raised TypeError — the mic gate stayed shut and « Hey coach » was never heard in WoW."""
    from treeaicoach import engine_coaching as ec
    from treeaicoach.wow import ai_context
    monkeypatch.setattr(ec.threading, "Thread", _SyncThread)
    monkeypatch.setattr(ai_context, "_lookup", lambda *a: None)       # no running mode: the engine flag decides
    eng = _engine()
    eng._wow_mode = True                                               # what mode.attach's on_change does
    eng._wow_db = E.scenario("full")["db"]
    monkeypatch.setattr(ai_context, "live_state", lambda engine=None: {})
    monkeypatch.setattr(ai_context, "load_plan", lambda engine=None: None)
    assert eng._wow_on() is True
    should_listen = (lambda: bool(getattr(eng, "_in_game", False)) or eng._wow_on())
    assert should_listen() is True
    assert eng.ask_ai(question="on fait quoi ?", spoken=True) == "Réponse tirée de tes données de jeu…"   # AI off
    assert eng.said and "Le cristal de Satyr" in eng.said[-1]          # AI off: answered from the facts
    eng._discord_voice_question("Paul", "je vends quoi ?")
    assert eng.calls[-1][0] == "ai_answer" and "Cuir moyen" in eng.calls[-1][1]
    eng._wow_mode = False
    assert eng._wow_on() is False


def test_lol_general_question_unchanged(monkeypatch):
    from treeaicoach import engine_coaching as ec
    monkeypatch.setattr(ec.threading, "Thread", _SyncThread)
    monkeypatch.setattr(ac, "_lookup", lambda *a: None)
    monkeypatch.setattr(ac, "answer_question", lambda *a, **k: pytest.fail("WoW path in LoL mode"))
    eng = _engine()
    eng._wow_mode = False
    eng._general_question("Bonjour ?", local=True)
    assert eng.said[-1] == "Conseil IA désactivé : choisis un fournisseur dans Réglages."


def test_legacy_fallback_when_facts_module_is_missing(monkeypatch):
    """A patched exe without treeaicoach/wow/ai_facts.py still answers (legacy context, no crash)."""
    monkeypatch.setattr(ac, "F", None)
    assert ac.intents("on fait quoi ?") == ["default"] and ac.local_answer("on fait quoi ?", {"x": 1}) == ""
    assert ac.question_context("on fait quoi ?", db={"char": {"level": 9}}, facts=[]) == {"niveau": 9}


def test_live_chat_with_an_old_reload_is_labelled_honestly():
    sc = E.scenario("full")
    sc["db"]["chars"][E.KEY]["summary"]["updated"] = E.NOW - 3600            # /reload 1 h ago, chat log live
    ctx = ac.question_context("combien d'xp par heure ?", db=sc["db"], live=sc["live"], plan=None, facts=[], now=E.NOW)
    assert ctx["joueur"]["age"].startswith("il y a 1 h") and ctx["joueur"]["zone"] == "Orneval (en direct)"
    assert "age" not in ctx["xp"] and ctx["xp"]["xp_par_heure"] == "21 500"   # the live measure is not old
    assert F.anonymize("4 ours enragés tués par Ours", {"Ours": "Prêtre 27"}) == "4 ours enragés tués par Prêtre 27"


def test_dead_damage_and_long_trips():
    ctx = ctx_for("je suis mort, je fais quoi ?")
    assert "groupe" not in ctx and ac.local_answer("je suis mort, je fais quoi ?", ctx).startswith("Retourne à ton corps")
    dmg = ac.local_answer("qui fait le plus de dégâts ?", ctx_for("qui fait le plus de dégâts ?"))
    assert dmg == "En tête du dernier compteur de dégâts en donjon : Mage 27."      # the name became a label
    d = ctx_for("ça vaut le coup d'aller aux Paluns pour une quête ?")["calcul_detour"]
    assert d["quetes"] == 1 and "Zeppelin" in d["trajet_aller"] and "approximatif" in d["precision"]


def test_non_hunter_pet_question_is_not_missing_data():
    sc = E.scenario("full")
    s = sc["db"]["chars"][E.KEY]["summary"]
    s.update({"class": "MAGE", "className": "Mage"})
    s.pop("hunter")
    ctx = ac.question_context("mon familier ?", db=sc["db"], live={}, plan=None, facts=[], now=E.NOW)
    assert ac.local_answer("mon familier ?", ctx) == "TreeAI ne suit que les familiers de chasseur."
