"""WoW « Hey coach » eval: the 30 questions of tests/wow/ai_eval_cases.py asked to the REAL model, or answered
without AI (``--local``), with the checks the tests apply to the facts.

Usage (the key is read from the environment ONLY, never printed / logged / written)::

    python -m tools.wow_ai_eval --local                       # no request: the answers said without AI
    GROQ_API_KEY=... python -m tools.wow_ai_eval [--provider groq] [--model M] [--only next,xph] [--pace 8]

Per question it prints the answer, its latency, and three checks: SHORT (≤ 3 sentences, ≤ 320 characters),
GROUNDED (every number of the answer is in the facts sent or in the question) and KEY (the expected key fact is
said, accents and case ignored). One request per question: mind the free-tier per-minute token limit
(Groq: 8 000 TPM ≈ 5 questions a minute with ``--pace 8``). The daily request counter is kept in memory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "wow"))

import ai_eval_cases as E  # noqa: E402

from treeaicoach import ai_advisor as ai  # noqa: E402
from treeaicoach.wow import ai_context as ac  # noqa: E402
from treeaicoach.wow import ai_facts as F  # noqa: E402

ENV_KEYS = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY", "ollama": ""}


def _nums(text: str) -> set[str]:
    return {re.sub(r"\s", "", m) for m in re.findall(r"\d[\d  ]*\d|\d", text)}


def check(case: dict, ctx: dict, answer: str) -> dict[str, bool]:
    state = json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))
    sentences = [s for s in re.split(r"(?<=[.!?…])\s+", answer.strip()) if s]
    allowed = _nums(state) | _nums(case["q"]) | {"1", "2", "3"}
    keys = case.get("local") or []
    return {"SHORT": len(sentences) <= 3 and len(answer) <= 320,
            "GROUNDED": _nums(answer) <= allowed,
            "KEY": (not keys) or any(F.fold(k) in F.fold(answer) for k in keys)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--provider", default="groq")
    ap.add_argument("--model", default="")
    ap.add_argument("--only", default="", help="comma-separated case ids")
    ap.add_argument("--local", action="store_true", help="no request: the answers said without AI")
    ap.add_argument("--pace", type=float, default=8.0, help="seconds between requests")
    a = ap.parse_args(argv)
    key = "" if a.local else os.environ.get(ENV_KEYS.get(a.provider, ""), "")
    provider = "off" if a.local else a.provider
    if not a.local and a.provider != "ollama" and not key:
        print(f"Set {ENV_KEYS.get(a.provider) or 'the API key'} in the environment, or use --local.")
        return 2
    ai.DAILY_QUOTA = ai.DailyQuota(path="")          # in memory: never touches the app's counter
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    totals = {"SHORT": 0, "GROUNDED": 0, "KEY": 0}
    n = 0
    for case in E.CASES:
        if only and case["id"] not in only:
            continue
        sc = E.scenario(case["scenario"])
        ctx = ac.question_context(case["q"], db=sc["db"], live=sc["live"], plan=sc["plan"], facts=sc["facts"],
                                  now=E.NOW)
        t0 = time.monotonic()
        answer = ac.answer_question(provider, key, a.model, case["q"], ctx=ctx)
        dt = time.monotonic() - t0
        res = check(case, ctx, answer)
        n += 1
        for k, ok in res.items():
            totals[k] += int(ok)
        flags = " ".join(f"{k}={'ok' if ok else 'NO'}" for k, ok in res.items())
        print(f"[{case['id']}] {case['q']}\n  → {answer}\n  {dt:.1f} s · {flags}")
        if not a.local and a.pace > 0:
            time.sleep(a.pace)
    if n:
        print("\n" + " · ".join(f"{k} {v}/{n}" for k, v in totals.items()))
    return 0 if n and all(v == n for v in totals.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
