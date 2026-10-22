"""ai_calm.py: automatic AI requests only while dead or just after my kill with nobody near."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treeaicoach import ai_calm  # noqa: E402


def g(kills=0, dead=False):
    return SimpleNamespace(me=SimpleNamespace(is_dead=dead, scores={"kills": kills}))


def enemy(uv, visible=True):
    return SimpleNamespace(relation="enemy", visible=visible, last_seen=0.0, position=lambda: uv)


def test_dead_is_calm_alive_is_not():
    gate = ai_calm.CalmGate()
    assert gate.update(1.0, g(dead=True)) is True
    assert gate.update(2.0, g(), (0.5, 0.5)) is False


def test_after_my_kill_calm_only_without_enemy_near_and_for_a_while():
    gate = ai_calm.CalmGate()
    gate.update(0.0, g(0), (0.5, 0.5))
    assert gate.update(1.0, g(1), (0.5, 0.5)) is True
    assert gate.update(2.0, g(1), (0.5, 0.5), [enemy((0.55, 0.5))]) is False      # an enemy right there
    assert gate.update(3.0, g(1), (0.5, 0.5), [enemy((0.9, 0.1))]) is True        # far away
    assert gate.update(4.0, g(1), (0.5, 0.5), threat=1) is False
    assert gate.update(1.0 + ai_calm.KILL_CALM_S + 1.0, g(1), (0.5, 0.5)) is False


def test_unknown_position_is_not_calm_and_never_raises():
    gate = ai_calm.CalmGate()
    gate.update(0.0, g(0))
    assert gate.update(1.0, g(1), None) is False
    assert gate.update(2.0, None) is False
