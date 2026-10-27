"""Contradiction arbiter (treeaicoach/arbiter.py): danger > fight > objective > plates > macro > tip."""
from __future__ import annotations

import os
import sys

import pytest

from treeaicoach import arbiter as A


def test_priority_order_documented():
    assert A.PRIORITY == ("danger", "fight", "objective", "plates", "macro", "tip")


def test_stances_and_sources():
    assert A.stance_of("Recule vers ta tour") == "retreat"
    assert A.stance_of("Plaque la tour (18 s)") == "push"
    assert A.stance_of("Rentre en base : 1 300 PO") == "recall"
    assert A.stance_of("Ne rentre pas : la vague arrive") == "stay"
    assert A.stance_of("Va bot : Dragon dans 0:45") is None
    assert A.source_of("GANK ! Recule") == "danger"
    assert A.source_of("Plaque la tour (18 s)", macro_text="Plaque la tour (18 s)", macro_kind="plates") == "plates"
    assert A.source_of("Prends le dragon : 2 morts") == "objective"
    assert A.source_of("Pousse ta vague") == "tip"


def test_danger_beats_macro_push_in_the_same_tick():
    lines = ["Plaque la tour (18 s)", "Pousse ta vague et récupère des plaques", "Recule : 2 contre 1"]
    out = A.order_lines(lines, macro_text=lines[0], macro_kind="plates")
    assert out == ["Recule : 2 contre 1"]


def test_objective_beats_macro_and_tip_and_keeps_agreeing_lines():
    lines = ["Rentre en base : 1 300 PO", "Prends le dragon : 2 ennemis morts", "Ne rentre pas : vague grosse"]
    out = A.order_lines(lines, tone=lambda _t: "info")
    # recall (tip) vs stay (tip): the earliest of equal rank wins; the objective push stays
    assert out[0] == "Rentre en base : 1 300 PO" and "Ne rentre pas : vague grosse" not in out
    assert "Prends le dragon : 2 ennemis morts" in out


def test_no_conflict_keeps_engine_order():
    lines = ["Va bot : Dragon dans 0:45", "Achète une balise de contrôle"]
    assert A.order_lines(lines) is lines
    assert A.order_lines([]) == [] and A.order_lines(None) is None   # never raises


SCENARIOS = ("gank_2v1", "fight_lost", "tower")


@pytest.mark.parametrize("name", SCENARIOS)
def test_ux_replay_scenarios_single_instruction(name, monkeypatch):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    ux = pytest.importorskip("tools.ux_replay")
    seen: list[tuple[list[str], list[str]]] = []
    real = A.order_lines

    def spy(lines, **kw):
        out = real(lines, **kw)
        seen.append((list(lines or []), list(out or [])))
        return out

    monkeypatch.setattr(A, "order_lines", spy)
    rp = ux.run(name, "debutant")
    vs = ux.judge(rp)
    assert not [v for v in vs if v.rule == "contradiction"], [v.line() for v in vs]
    assert seen, "the HUD line never went through the arbiter"
    for _inp, out in seen:
        st = {A.stance_of(x) for x in out} - {None}
        assert not any(A.OPPOSITE[s] in st for s in st), out
