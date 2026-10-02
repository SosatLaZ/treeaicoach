"""LIVE check of the AI advisor against a real provider (default Groq): the same requests as in game.

Usage (the key is read from the environment ONLY, never printed / logged / written)::

    GROQ_API_KEY=... python -m tools.ai_live_check [--provider groq] [--model M] [--only plan,review]

Scenarios (one request each unless said): model list; a "mastermind" plan at three moments
(objective window with the card active, comeback, manual question) from the bundled Live Client
fixture + a full synthetic analysis context; the "Tester" button path (check_connection); the
"Tester la clé" path (ui_kit.test_ai_key); the post-game review of the bundled report. It prints
the latency, the prompt / completion tokens (from the provider's ``usage``), the parsed plan and
the checks applied in game (JSON schema, French, verb first, item grounding, card consistency).
About 7 requests in total: mind the free-tier per-minute token limit (Groq: 8 000 TPM).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from treeaicoach import ai_advisor as ai  # noqa: E402

ENV_KEYS = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY", "ollama": ""}
_IMPERATIVE = re.compile(r"^(?:[A-ZÉÈÀÂ][a-zéèêàâîôûç'’-]*(?:s|e|ez|x|ds|ts)?)\b")


def _fixture_game():
    from treeaicoach.live_client import parse_allgamedata

    return parse_allgamedata(json.loads((ROOT / "tests" / "fixtures" / "allgamedata_sample.json")
                                        .read_text(encoding="utf-8")))


def _context(card: str | None) -> dict:
    g = _fixture_game()
    ours = [g.me.champion_alias] + [p.champion_alias for p in g.allies]
    theirs = [p.champion_alias for p in g.enemies]
    ctx = {"ph": "milieu",
           "diff": {"eq": -1400, "kills": "7-10", "po": -450, "niv": -1, "cs": -18, "vs": "Darius",
                    "vs_alias": "Darius"},
           "conseils": ["Recule quand il a 5 saignements sur toi : son R t'exécute",
                        "Échange court avec ton Q quand il rate son E"],
           "compo": {"nous": ai.team_profile(ours), "eux": ai.team_profile(theirs)},
           "effets": ai._ctx_effects(None, g, 0.0) or {"anti-soin": ["Darius"]},
           "jgl": {"c": "Lee Sin", "lv": 11, "vu": "rivière du bas, il y a 14 s", "farm": "bot"},
           "carto": {"dragons": "1-2", "tours_perdues": "nous 2, eux 1"},
           "causes": [[int(g.game_time) - 200, "Recule plus tôt : mort à 1 contre 2 (Lee Sin est venu)"]],
           "coups": ["10:40 blunder : Mort dans le brouillard sans balise.", "12:05 great : Plaque prise seul."],
           "prec": 61, "prio": {"bot": "vague chez nous", "mid": "équilibrée", "top": "prio (vague chez eux)"},
           "balises": ["Pixel du bas", "Rivière du bas"], "wp": 42}
    if card:
        ctx["carte"] = {"appel": card, "pourquoi": "Leur jungler réapparaît dans 40 s."}
    return ctx


def _raw_call(prov: str, key: str, model: str, prompt: str) -> tuple[str, dict, float]:
    """One request like the advisor's (system prompt + JSON mode), returning the raw text, the
    provider ``usage`` and the latency."""
    url, headers, body = ai.build_request(prov, model, key, ai.system_prompt(), prompt, None,
                                          ai.PLAN_MAX_TOKENS, json_mode=True)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    t0 = time.monotonic()
    try:
        with ai._opener_for(url).open(req, timeout=ai.TIMEOUT_S + 6) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        kind, wait = ai._classify_http(exc.code, exc.read(4096).decode("utf-8", "replace"), exc.headers)
        raise ai.AIError(kind, f"HTTP {exc.code}", retry_after=wait) from None
    dt = time.monotonic() - t0
    return ai.parse_response(prov, data, raw=True), data.get("usage") or data.get("usageMetadata") or {}, dt


def _checks(text: str, plan: dict | None, snap: dict, card: str | None) -> list[str]:
    out = []
    out.append("json ok" if plan else "JSON CASSÉ")
    if plan:
        first = [plan["plan"]] + list(plan["etapes"])
        verbs = [bool(_IMPERATIVE.match(x)) for x in first]
        out.append(f"verbe en tête {sum(verbs)}/{len(verbs)}")
        en = ai._EN_WORDS.findall(" ".join(first)) + re.findall(r"\b(ward|push|lane|farm|gank|back)\b", " ".join(first), re.I)
        out.append("français" if not en else f"ANGLAIS: {sorted(set(en))}")
        out.append("objets OK" if ai.validate_item_advice(ai.plan_text(plan), _fixture_game(),
                                                          snap.get("objets_possibles") or []) else "OBJET INVENTÉ")
        if card:
            v = ai.card_check(ai.plan_text(plan), card, stance=plan.get("carte"), why=plan.get("pourquoi"),
                              goal=plan.get("objectif"))
            out.append(f"carte: {plan.get('carte')} -> {v}")
        leak = [k for k in ("objt", "jgl", "objets_possibles", "me.it", "carto") if k in text]
        if leak:
            out.append(f"CLÉS JSON CITÉES: {leak}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--provider", default="groq", choices=sorted(ai.PROVIDERS))
    ap.add_argument("--model", default="")
    ap.add_argument("--only", default="", help="comma list: models,plan,connection,key,review")
    args = ap.parse_args(argv)
    prov = args.provider
    env = ENV_KEYS.get(prov, "")
    key = os.environ.get(env, "") if env else ""
    if env and not key:
        print(f"Set {env} in the environment (the key is never printed).")
        return 2
    only = {x.strip() for x in args.only.split(",") if x.strip()}

    def want(k: str) -> bool:
        return not only or k in only

    cfg = NS(ai_provider=prov, ai_api_key=key, ai_model=args.model)
    n = 0
    if want("models") and prov in ("groq", "openrouter", "gemini"):
        ids = ai.list_models(prov, key, timeout=15)
        n += 1
        print(f"[models] {len(ids)} : pick = {ai.pick_model(prov, ids)} ; chat = "
              f"{sorted(i for i in ids if not any(w in i.lower() for w in ai._NOT_CHAT))}")
    if want("plan"):
        g = _fixture_game()
        objs = [NS(key="dragon", name="Dragon", alive=False, remaining=70.0),
                NS(key="baron", name="Baron", alive=False, remaining=240.0)]
        for moment, card in (("objective", "Prends le dragon maintenant : Lee Sin est mort"),
                             ("comeback:gold", "Recule vers ta tour : Darius est niveau 6, pas toi"),
                             ("manual", None)):
            snap = ai.build_snapshot(g, moment=moment, context=_context(card), objectives=objs,
                                     item_text="Prochain objet : Gage de Sterak.", trend="-900 en 2 min")
            prompt = ai.build_prompt(snap)
            try:
                text, usage, dt = _raw_call(prov, key, args.model, prompt)
            except ai.AIError as exc:
                print(f"[plan {moment}] ERREUR {exc.code} ({exc.detail}, attendre {exc.retry_after})")
                n += 1
                continue
            n += 1
            plan = ai.parse_plan(text)
            print(f"[plan {moment}] {dt:.1f} s, snapshot ~{ai.snapshot_tokens(snap)} tokens estimés, usage {usage}")
            print("   ", ai.plan_text(plan) if plan else text[:300])
            print("   ", " | ".join(_checks(text, plan, snap, card)))
    if want("connection"):
        t0 = time.monotonic()
        print(f"[Tester] {ai.check_connection(cfg)} ({time.monotonic() - t0:.1f} s)")
        n += 1
    if want("key"):
        from treeaicoach import ui_kit

        print(f"[Tester la clé] {ui_kit.test_ai_key(cfg)}")
        n += 1
    if want("review"):
        from treeaicoach.analysis import analyze_game

        a = analyze_game(json.loads((ROOT / "tests" / "fixtures" / "game_record_sample.json").read_text(encoding="utf-8")))
        t0 = time.monotonic()
        review = ai.postgame_review(cfg, a, timeline=["10:00 or -300 kills 3-4 moi niv 9 1/1/2 70 cs",
                                                      "14:00 plan IA : Pousse ta vague puis va au dragon."])
        n += 1
        print(f"[revue] {time.monotonic() - t0:.1f} s\n{review}")
    print(f"({n} requêtes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
