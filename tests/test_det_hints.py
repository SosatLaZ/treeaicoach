"""Generic-detector hints -> stack proposals (roster_matcher.HINT_*)."""

from __future__ import annotations

from types import SimpleNamespace

import treeaicoach.roster_matcher as RM


def _matcher():
    m = RM.RosterMatcher.__new__(RM.RosterMatcher)
    m._state = RM._State()
    return m


def test_hints_keep_only_detections_next_to_an_accepted_icon():
    m = _matcher()
    m._state.frames = 5
    D = 0.1
    acc = [SimpleNamespace(x=0.5, y=0.5)]                 # kx = ky = 1: normalized coords
    m.set_partial_hints([(0.5 + 0.6 * D, 0.5, "ally", 0.8),     # beside: kept
                         (0.5 + 0.1 * D, 0.5, "ally", 0.9),     # the same icon: no
                         (0.5 + 2.0 * D, 0.5, "enemy", 0.9),    # far: a normal extra
                         (0.5, 0.5 + 0.6 * D, "enemy", 0.2)])   # weak: no
    props = m._hint_props(acc, 1.0, 1.0, D)
    assert [(p[1], round(p[2], 3)) for p in props] == [("ally", 0.56)]
    assert props[0][0] > 1.0                               # ranked before the probes
    m._state.frames = 5 + RM.HINT_FRAMES + 1                  # stale
    assert m._hint_props(acc, 1.0, 1.0, D) == []


def test_set_partial_hints_never_raises():
    m = _matcher()
    m.set_partial_hints([("bad",)])
    assert m._hint_props([], 1.0, 1.0, 0.1) == []
    m.set_partial_hints(None)
    assert m._hint_props([SimpleNamespace(x=0.5, y=0.5)], 1.0, 1.0, 0.1) == []
