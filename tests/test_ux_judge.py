"""UX replay judge (tools/ux_replay.py): what the player perceives on scripted games.

The judge itself is checked on synthetic transcripts (it must catch the classic mistakes), then
the real engine is replayed on the scripted scenarios with a ceiling on violations.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import ux_replay as ux  # noqa: E402

#: violations allowed per level over all scenarios (lower it, never raise it)
CEILING = {"debutant": 0, "intermediaire": 0}


def _replay(frames: list, **kw) -> ux.Replay:
    sc = ux.Scenario("synthetic", "synthétique", 0.0, 200.0, warmup=0.0, **kw)
    return ux.Replay(sc, "debutant", frames, 0.0)


def _rules(vs: list) -> set[str]:
    return {v.rule for v in vs}


def test_judge_flags_abstract_long_english_and_vous() -> None:
    frames = [ux.Frame(float(t), ("vert", "Phase de voie finie : jouez groupés autour des objectifs"))
              for t in range(40, 50)]
    frames += [ux.Frame(float(t), ("vert", "Push the wave — vite")) for t in range(60, 70)]
    rules = _rules(ux.judge(_replay(frames)))
    assert {"texte:abstrait", "texte:vouvoiement", "texte:anglais", "texte:tiret"} <= rules


def test_judge_flags_wrong_state() -> None:
    frames = [ux.Frame(float(t), ("vert", "Pousse ta vague"), near=(2, 0), hp=0.5) for t in range(40, 60)]
    frames += [ux.Frame(float(t), ("gris", "Pousse ta vague vers leur tour"), dead=True) for t in range(60, 70)]
    vs = ux.judge(_replay(frames, danger=[(45.0, 55.0, "2 contre 1")]))
    assert {"état:pas-rouge", "état:vert-entouré", "état:voie-mort"} <= _rules(vs)


def test_judge_flags_contradiction_flicker_and_silence() -> None:
    frames = []
    for t in range(30, 60):
        text = "Pousse ta vague" if t < 40 else "Joue prudent : reste sous ta tour" if t < 42 else "Balise ta rivière"
        frames.append(ux.Frame(float(t), ("vert", text)))
    vs = ux.judge(_replay(frames, contacts=[(55.0, "gank")]))
    assert {"contradiction", "flicker:carte", "silence:contact"} <= _rules(vs)


def test_judge_flags_voice_outside_whitelist() -> None:
    frames = [ux.Frame(40.0, None, voice=[("Pense à acheter une balise.", "control_ward|control_ward", 0)])]
    assert "voix:hors-liste" in _rules(ux.judge(_replay(frames)))


@pytest.mark.parametrize("level", sorted(CEILING))
def test_scenarios_within_ceiling(level: str) -> None:
    bad = []
    for name in ux.SCENARIOS:
        rp = ux.run(name, level)
        bad += [f"{name} {v.line()}" for v in ux.judge(rp)]
    assert len(bad) <= CEILING[level], "\n".join(bad)


def test_card_line_is_verb_first_and_stance_is_detected() -> None:
    from treeaicoach.presenter import card_line, line_stance

    assert card_line("Darius est mort : pousse ta vague et prends la tour.") == \
        "Pousse ta vague et prends la tour : Darius est mort"
    assert card_line("Combat gagné 3 à 1 !") is None                 # a statement is no card line
    assert line_stance("Pousse ta vague : Darius est mort") == "push"
    assert line_stance("Joue prudent : reste sous ta tour") == "retreat"
    assert line_stance("Balise la rivière : ta vague pousse") is None
