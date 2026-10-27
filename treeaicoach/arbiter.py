"""Contradiction arbiter: the ONE place that decides which engine's instruction wins when the
engines disagree (docs/ARCHITECTURE.md § 27).

Each engine speaks for itself (``danger.py`` "Recule !", ``fight.py`` / the director "Engage",
``objectives`` / ``objective_planner`` "Va bot : Dragon dans 0:45", ``macro`` plates "Plaque la
tour", the other ``macro`` calls, ``tips`` / ``coach``). The HUD card holds ONE instruction, so when
two candidate lines pull in opposite directions (push / retreat, recall / stay) only the line of
the highest-priority source is kept and the lower ones that contradict it are dropped:

    danger > fight > objective > plates > macro > tip

* ``danger``    a gank / 2 v 1 / low-HP alarm, any line of tone "danger" (your life first);
* ``fight``     an engage / disengage decision during a teamfight;
* ``objective`` an epic objective call (dragon, Baron, Herald, grubs, Elder);
* ``plates``    turret plates / tower hits while the lane opponent is away;
* ``macro``     the other genius calls of ``macro.py`` (cross-map, jungler dead, freeze...);
* ``tip``       coach insights and rotating tips.

When the candidates do not disagree the order chosen by the engine (freshness, dwell, ...) is kept
unchanged: the arbiter only settles conflicts. Pure, no I/O, never raises.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

#: source priority, strongest first
PRIORITY: tuple[str, ...] = ("danger", "fight", "objective", "plates", "macro", "tip")
_RANK = {s: i for i, s in enumerate(PRIORITY)}

#: opposite stances: a line with one of them never coexists with a stronger line with the other
OPPOSITE = {"push": "retreat", "retreat": "push", "recall": "stay", "stay": "recall"}

_CLAUSE = r"(?:^|[.:;!,?]\s*|\b(?:et|puis|alors|donc)\s+)"
_DANGER_RE = re.compile(r"(?i)\b(gank|danger|recule|fuis|2 contre 1|3 contre 1|base attaquée|en infériorité|"
                        r"peu de vie)\b")
_FIGHT_RE = re.compile(r"(?i)\b(engage|attaque|combat|focus|bats-toi|ace|regroupe)\b")
_OBJECTIVE_RE = re.compile(r"(?i)\b(dragon|baron|héraut|larves?|ancestral|nashor)\b")
_PLATES_RE = re.compile(r"(?i)\b(plaques?|frappe (la|leur|une|les) tours?|prends la tour)\b")
_PUSH_RE = re.compile(r"(?i)" + _CLAUSE + r"(pousse|plaque|frappe (la|leur|une|les) tours?|attaque|engage|vas-y|"
                      r"joue agressif|punis|mets la pression|va taper|prends (le|la|les) )")
_RETREAT_RE = re.compile(r"(?i)" + _CLAUSE + r"(recule|reste sous ta tour|reste près de ta tour|arrête de pousser|"
                         r"ne pousse|fuis|joue prudent|ne (te )?bats pas|ne t'avance pas|laisse (le|la|les) )")
_RECALL_RE = re.compile(r"(?i)\b(rentre|retourne en base|retour en base|fais un retour|back maintenant)\b")
_STAY_RE = re.compile(r"(?i)\b(ne rentre pas|reste en (voie|ligne)|reste sur (ta|la) (voie|vague)|ne back pas)\b")


@dataclass(frozen=True)
class Claim:
    """One candidate instruction: its text, the engine family that produced it, its stance."""

    text: str
    source: str
    stance: str | None


def stance_of(text: str | None) -> str | None:
    """``"push"`` / ``"retreat"`` / ``"recall"`` / ``"stay"`` / None (no clear direction)."""
    t = str(text or "")
    if not t:
        return None
    if _STAY_RE.search(t):
        return "stay"
    if _RECALL_RE.search(t):
        return "recall"
    r, p = bool(_RETREAT_RE.search(t)), bool(_PUSH_RE.search(t))
    if r and not p:
        return "retreat"
    if p and not r:
        return "push"
    return None


def source_of(text: str | None, *, macro_text: str | None = None, macro_kind: str | None = None,
              tone: str | None = None) -> str:
    """Engine family of a candidate line (see :data:`PRIORITY`)."""
    t = str(text or "")
    if tone == "danger" or _DANGER_RE.search(t):
        return "danger"
    if macro_text is not None and t == macro_text:
        k = str(macro_kind or "")
        if k.startswith("fight"):
            return "fight"
        if k == "plates":
            return "plates"
        return "objective" if _OBJECTIVE_RE.search(t) else "macro"
    if _FIGHT_RE.search(t):
        return "fight"
    if _OBJECTIVE_RE.search(t):
        return "objective"
    if _PLATES_RE.search(t):
        return "plates"
    return "tip"


def claims(lines: Iterable[str], *, macro_text: str | None = None, macro_kind: str | None = None,
           tone: Callable[[str], str] | None = None) -> list[Claim]:
    out = []
    for v in lines:
        tn = None
        if tone is not None:
            try:
                tn = tone(v)
            except Exception:
                tn = None
        out.append(Claim(str(v), source_of(v, macro_text=macro_text, macro_kind=macro_kind, tone=tn),
                         stance_of(v)))
    return out


def decide(cands: list[Claim]) -> tuple[list[Claim], list[tuple[Claim, Claim]]]:
    """``(kept, dropped)``: ``kept`` = the candidates in their order, minus every claim contradicted
    by a stronger one; when a conflict exists the strongest claim moves first (it is THE
    instruction). ``dropped`` = ``(loser, winner)`` pairs (diagnostics, tests)."""
    if len(cands) < 2:
        return list(cands), []
    ranked = sorted(range(len(cands)), key=lambda i: (_RANK.get(cands[i].source, len(PRIORITY)), i))
    dropped: list[tuple[Claim, Claim]] = []
    lost: set[int] = set()
    for pos, i in enumerate(ranked):
        if i in lost:
            continue
        c = cands[i]
        if c.stance is None:
            continue
        for j in ranked[pos + 1:]:
            if j in lost:
                continue
            o = cands[j]
            if o.stance is not None and OPPOSITE.get(c.stance) == o.stance:
                lost.add(j)
                dropped.append((o, c))
    if not dropped:
        return list(cands), []
    kept = [cands[i] for i in range(len(cands)) if i not in lost]
    winner = dropped[0][1]
    kept.remove(winner)
    return [winner] + kept, dropped


def order_lines(lines: list[str], *, macro_text: str | None = None, macro_kind: str | None = None,
                tone: Callable[[str], str] | None = None) -> list[str]:
    """The HUD candidate lines after arbitration (unchanged when they agree). Never raises."""
    try:
        if not lines or len(lines) < 2:
            return lines
        kept, dropped = decide(claims(lines, macro_text=macro_text, macro_kind=macro_kind, tone=tone))
        return lines if not dropped else [c.text for c in kept]
    except Exception:
        return lines
